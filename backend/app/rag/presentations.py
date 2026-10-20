"""Read-only PowerPoint slide rendering in a bounded COM worker process."""
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep


def _powerpoint_application():
    import win32com.client
    import pywintypes
    try:
        return win32com.client.DispatchEx('PowerPoint.Application')
    except pywintypes.com_error as error:
        if error.hresult != -2147221164:
            raise
        # WPS can replace the PowerPoint COM registration. Start installed Office
        # directly without changing system registration or document associations.
        roots = {os.environ.get('ProgramFiles', ''), os.environ.get('ProgramFiles(x86)', '')}
        candidates = [Path(root) / 'Microsoft Office' / variant / 'POWERPNT.EXE'
                      for root in roots if root
                      for variant in ('root/Office16', 'Office16', 'Office15')]
        executable = next((p for p in candidates if p.is_file()), None)
        if executable is None:
            raise
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        subprocess.Popen([str(executable), '/automation'], startupinfo=startup,
                         creationflags=subprocess.CREATE_NO_WINDOW,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = monotonic() + 8
        while monotonic() < deadline:
            sleep(.25)
            try:
                return win32com.client.DispatchEx('PowerPoint.Application')
            except pywintypes.com_error:
                continue
        raise ValueError('已找到PowerPoint，但无法连接COM自动化接口，请检查Office会话及COM注册') from error


def _close_powerpoint(application, presentation, previous_security):
    import pywintypes
    try:
        # Some Office versions exit when the last hidden presentation closes.
        if presentation is not None:
            presentation.Close()
        if application is not None and previous_security is not None:
            application.AutomationSecurity = previous_security
        if application is not None and application.Presentations.Count == 0:
            application.Quit()
    except pywintypes.com_error as error:
        if error.hresult not in {-2147023170, -2147023174, -2147417848}:
            raise


def render_slides(path: Path) -> list[bytes]:
    from app.config import get_settings
    settings = get_settings()
    if sys.platform != 'win32':
        raise ValueError('PPT/PPTX渲染需要Windows及本机PowerPoint')
    with TemporaryDirectory(prefix='qwen-slides-') as folder:
        try:
            completed = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), str(path.resolve()), folder,
                 str(settings.ocr_max_pages)], capture_output=True, text=True,
                encoding='utf-8', timeout=settings.presentation_render_timeout_seconds,
                creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired as exc:
            raise ValueError('PowerPoint渲染超时，未将未完成幻灯片送入OCR') from exc
        try:
            report = json.loads(completed.stdout)
        except (ValueError, TypeError) as exc:
            raise ValueError('PowerPoint渲染失败，未取得有效转换记录') from exc
        if completed.returncode or report.get('error'):
            raise ValueError('PowerPoint转换失败：' + report.get('error', '未知错误'))
        count = report.get('count')
        if type(count) is not int or not 1 <= count <= settings.ocr_max_pages:
            raise ValueError('PowerPoint转换页数无效或超出预算')
        files = [Path(folder) / f'slide-{i:04d}.png' for i in range(1, count + 1)]
        if not all(p.is_file() and p.stat().st_size > 0 for p in files):
            raise ValueError('PowerPoint未完成全部幻灯片渲染')
        if sum(p.stat().st_size for p in files) > 50 * 1024 * 1024:
            raise ValueError('PowerPoint渲染图像超过50MB预算，请拆分附件')
        return [p.read_bytes() for p in files]


def _render_worker(source, directory, max_pages):
    import pythoncom
    pythoncom.CoInitialize()
    application = presentation = None
    previous_security = None
    try:
        application = _powerpoint_application()
        if application.Name != 'Microsoft PowerPoint':
            raise ValueError('COM接口未连接Microsoft PowerPoint，请检查Office/WPS注册冲突')
        previous_security = application.AutomationSecurity
        application.AutomationSecurity = 3  # msoAutomationSecurityForceDisable
        presentation = application.Presentations.Open(str(Path(source).resolve()), -1, 0, 0)
        count = presentation.Slides.Count
        if not 1 <= count <= max_pages:
            raise ValueError('幻灯片页数超过解析预算或文件为空')
        width = 2000
        height = max(1, round(width * presentation.PageSetup.SlideHeight / presentation.PageSetup.SlideWidth))
        if height > 3000:
            width = max(1, round(width * 3000 / height))
            height = 3000
        for index in range(1, count + 1):
            presentation.Slides.Item(index).Export(
                str(Path(directory) / f'slide-{index:04d}.png'), 'PNG', width, height)
        return {'count': count}
    finally:
        try:
            _close_powerpoint(application, presentation, previous_security)
        finally:
            pythoncom.CoUninitialize()


if __name__ == '__main__':
    try:
        result = _render_worker(sys.argv[1], sys.argv[2], int(sys.argv[3]))
        print(json.dumps(result))
    except Exception as error:
        if getattr(error, 'hresult', None) == -2147221164:
            reason = 'PowerPoint COM类未注册，请修复Office安装/COM注册后重试'
        elif isinstance(error, ImportError):
            reason = '缺少pywin32，请安装backend/requirements.txt'
        else:
            reason = str(error) if isinstance(error, ValueError) else type(error).__name__
        print(json.dumps({'error': reason}, ensure_ascii=True))
        sys.exit(1)

"""Parse documents, create chunks and build retrieval indexes."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.indexer import build_index, get_local_index, knowledge_health
from app.config import get_settings


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--embeddings", action="store_true", help="调用已配置云端服务构建向量索引；默认本地关键词索引")
    parser.add_argument("--report", type=Path, help="保存逐文档解析与来源定位验收记录(JSON)")
    parser.add_argument("--status", action="store_true", help="只查看索引与断点状态，不解析或构建")
    parser.add_argument("--concurrency", type=int, choices=range(1, 17), help="向量构建并发数，范围1—16")
    args = parser.parse_args()
    if args.status:
        settings = get_settings()
        checkpoint = settings.indexes_path / 'semantic-build.json'
        print(json.dumps({'health': knowledge_health(),
            'checkpoint': json.loads(checkpoint.read_text(encoding='utf-8')) if checkpoint.exists() else None},
            ensure_ascii=False, indent=2))
        return
    if args.concurrency:
        get_settings().embedding_build_concurrency = args.concurrency
    result = await build_index(use_embeddings=args.embeddings)
    index = get_local_index()
    if args.report:
        settings = get_settings()
        report = {"synthetic": "合成" in settings.demo_data_label, "health": knowledge_health(),
                  "database_path": str(settings.database_path), "documents_path": str(settings.documents_path),
                  "indexes_path": str(settings.indexes_path), "build": result, "sources": []}
        for identifier, source in index["sources"].items():
            chunks = [c for c in index["chunks"] if c["doc_id"] == identifier]
            positions_valid = all(next(p["text"] for p in source["pages"] if p["page_no"] == c["page_no"])
                                  [c["start_char"]:c["end_char"]] == c["text"] for c in chunks)
            report["sources"].append({"filename": source["filename"], "source_hash": source["source_hash"],
                "format": Path(source["filename"]).suffix, "chunks": len(chunks), "exact_offsets_verified": positions_valid,
                "pages": [{"page_no": p["page_no"], "characters": len(p["text"]),
                           "extraction_method": p.get("extraction_method"),
                           "ocr_min_confidence": p.get("ocr_min_confidence")} for p in source["pages"]]})
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(result)
    print(f"知识库索引: {result['documents']} 份文档，{result['chunks']} 个片段，{len(result['issues'])} 项解析问题")
    if result["issues"] or not result["chunks"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())

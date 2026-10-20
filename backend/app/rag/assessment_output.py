"""Bound diagnostic presentation after all evidence checks have completed."""
from collections import Counter


def compact_rejection_details(assessment,limit=100):
    rejected=assessment.get('rejected') or []
    if len(rejected)<=limit:return
    assessment['rejected_summary']={'total':len(rejected),'shown':limit,
        'reasons':dict(Counter(item.get('reason','未标注原因') for item in rejected)),
        'details_truncated':True}
    assessment['rejected']=rejected[:limit]

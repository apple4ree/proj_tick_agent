"""
monitoring/reporters/verification_report.py
--------------------------------------------
Convert BatchVerificationReport to a JSON-serializable dict.
"""
from __future__ import annotations

from dataclasses import asdict


def build_verification_report(report) -> dict:
    """
    Convert a BatchVerificationReport to a JSON-serializable dict.

    Parameters
    ----------
    report : BatchVerificationReport
    """
    def _failure_list(items):
        out = []
        for r in items:
            try:
                d = asdict(r)
            except Exception:
                d = {k: getattr(r, k, None) for k in vars(r)}
            # Convert Timestamps to ISO strings
            for k, v in list(d.items()):
                try:
                    import pandas as pd
                    if isinstance(v, pd.Timestamp):
                        d[k] = v.isoformat()
                except Exception:
                    pass
            out.append(d)
        return out

    generated_at = report.generated_at
    try:
        import pandas as pd
        if isinstance(generated_at, pd.Timestamp):
            generated_at = generated_at.isoformat()
    except Exception:
        generated_at = str(generated_at)

    return {
        "run_id":       report.run_id,
        "generated_at": generated_at,
        "n_fills":      report.n_fills,
        "fee": {
            "pass_rate":  report.fee_pass_rate,
            "n_failures": len(report.fee_failures),
            "failures":   _failure_list(report.fee_failures),
        },
        "slippage": {
            "pass_rate":  report.slippage_pass_rate,
            "n_failures": len(report.slippage_failures),
            "failures":   _failure_list(report.slippage_failures),
        },
        "latency": {
            "pass_rate":  report.latency_pass_rate,
            "n_failures": len(report.latency_failures),
            "failures":   _failure_list(report.latency_failures),
        },
        "queue": {
            "pass_rate":  report.queue_pass_rate,
            "n_failures": len(report.queue_failures),
            "failures":   _failure_list(report.queue_failures),
        },
    }

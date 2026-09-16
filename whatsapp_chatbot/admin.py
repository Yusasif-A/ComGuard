"""
ComGuard command-line admin.

    python admin.py reports [--status submitted] [--limit 20]
    python admin.py show CG-7K3M9Q
    python admin.py verify CG-7K3M9Q --by "LASEMA desk 2"
    python admin.py dismiss CG-7K3M9Q --note "duplicate"
    python admin.py alerts
    python admin.py stats
    python admin.py forget <phone-number>

`verify` here only marks the report. It does NOT send a broadcast — that runs
through the API endpoint so an alert is always attributable to an authenticated
dispatcher rather than to whoever had shell access.

`forget` is the data-deletion path. It removes the person's settings and their
conversation log. It deliberately does NOT delete their reports: those carry a
pseudonym rather than a number, they may already be corroborating somebody
else's report of a real incident, and deleting them would quietly weaken an
active alert. Once the settings row is gone, nothing links the remaining reports
back to a person.
"""

import argparse
import json
import sys
from datetime import datetime

import reports as reports_module
import store


def _print_row(report: dict):
    created = report.get("created_at")
    when = created.strftime("%Y-%m-%d %H:%M") if isinstance(created, datetime) else str(created)
    location = report.get("location") or {}
    where = (
        f"{location['lat']:.3f},{location['lon']:.3f}"
        if location.get("lat") is not None else "no location"
    )
    print(
        f"{report.get('report_id', '?'):<12} {when:<17} "
        f"{report.get('status', '?'):<13} {report.get('category', '?'):<20} "
        f"{report.get('severity', '?'):<9} {where:<20} "
        f"{(report.get('summary') or '')[:60]}"
    )


def cmd_reports(args):
    rows = reports_module.recent_reports(
        limit=args.limit, status=args.status, category=args.category
    )
    if not rows:
        print("No reports found.")
        return 0

    print(f"{'REPORT':<12} {'WHEN':<17} {'STATUS':<13} {'CATEGORY':<20} "
          f"{'SEVERITY':<9} {'LOCATION':<20} SUMMARY")
    print("-" * 140)
    for row in rows:
        _print_row(row)
    print(f"\n{len(rows)} report(s)")
    return 0


def cmd_show(args):
    report = reports_module.get_report(args.report_id)
    if not report:
        print(f"No report with id {args.report_id}")
        return 1
    print(json.dumps(report, indent=2, default=str))

    matches = reports_module.find_corroborating(report)
    if matches:
        print(f"\nCorroborating reports ({len(matches)}):")
        for match in matches:
            print(f"  {match['report_id']}  {match.get('category')}  "
                  f"{match.get('_distance_km')}km away")
    else:
        print("\nNo corroborating reports found.")
    return 0


def cmd_verify(args):
    if not reports_module.get_report(args.report_id):
        print(f"No report with id {args.report_id}")
        return 1
    reports_module.mark_verified(args.report_id, dispatcher=args.by)
    print(f"✅ {args.report_id} marked verified by {args.by or 'cli'}")
    print("No broadcast was sent — use POST /reports/<id>/verify for that.")
    return 0


def cmd_dismiss(args):
    if not reports_module.get_report(args.report_id):
        print(f"No report with id {args.report_id}")
        return 1
    reports_module.mark_dismissed(args.report_id, dispatcher=args.by, note=args.note)
    print(f"🚫 {args.report_id} dismissed")
    return 0


def cmd_alerts(args):
    rows = reports_module.recent_alerts(limit=args.limit)
    if not rows:
        print("No alerts have been broadcast.")
        return 0
    for alert in rows:
        sent = alert.get("sent_at")
        when = sent.strftime("%Y-%m-%d %H:%M") if isinstance(sent, datetime) else str(sent)
        print(f"{when}  {alert.get('category'):<20} "
              f"{alert.get('recipients', 0)} recipient(s)  "
              f"report {alert.get('report_id')}")
    return 0


def cmd_stats(args):
    print(json.dumps(reports_module.report_stats(), indent=2, default=str))
    return 0


def cmd_forget(args):
    phone = args.phone.replace("+", "")
    settings = store.get_user_settings(phone)
    if not settings:
        print(f"No stored settings for {phone}")

    conversations, settings_collection = store._connect()
    if settings_collection is None:
        print("Database unavailable.")
        return 1

    removed_settings = settings_collection.delete_many({"user_id": phone}).deleted_count
    removed_messages = conversations.delete_many({"user_id": phone}).deleted_count

    print(f"🧹 Removed {removed_settings} settings row(s) and "
          f"{removed_messages} conversation record(s) for {phone}")
    print("Their reports were kept — they carry a pseudonym, not this number, "
          "and may be corroborating a live incident.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="ComGuard admin")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("reports", help="List recent reports")
    p.add_argument("--status", default="", help="draft/submitted/corroborated/verified/dismissed")
    p.add_argument("--category", default="")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=cmd_reports)

    p = sub.add_parser("show", help="Show one report in full")
    p.add_argument("report_id")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("verify", help="Mark a report as officially verified")
    p.add_argument("report_id")
    p.add_argument("--by", default="cli")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("dismiss", help="Dismiss a report")
    p.add_argument("report_id")
    p.add_argument("--by", default="cli")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_dismiss)

    p = sub.add_parser("alerts", help="List broadcast alerts")
    p.add_argument("--limit", type=int, default=25)
    p.set_defaults(func=cmd_alerts)

    p = sub.add_parser("stats", help="Headline report counts")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("forget", help="Erase a person's settings and message log")
    p.add_argument("phone")
    p.set_defaults(func=cmd_forget)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

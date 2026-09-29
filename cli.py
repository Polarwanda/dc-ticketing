#!/usr/bin/env python3
"""Command-line interface for the datacenter ticket system."""
import argparse

import core


def cmd_create(conn, a):
    tid = core.create_ticket(conn, a.title, a.type, a.priority,
                             a.site, a.rack, a.feed, a.asset)
    print(f"Created ticket #{tid}")


def cmd_list(conn, a):
    rows = core.list_tickets(conn, a.status, a.priority, a.tech, a.site, a.asset)
    if not rows:
        print("No tickets found.")
        return
    print(f"{'ID':<4}{'PRIORITY':<10}{'STATUS':<13}{'SLA':<10}{'RACK':<8}{'TECH':<10}TITLE")
    for r in rows:
        print(f"{r['id']:<4}{r['priority']:<10}{r['status']:<13}{core.sla_state(r):<10}"
              f"{r['rack'] or '-':<8}{r['assignee'] or '-':<10}{r['title']}")


def cmd_show(conn, a):
    t = core.get_ticket(conn, a.id)
    for key in t.keys():
        print(f"{key:>11}: {t[key]}")
    print(f"{'sla':>11}: {core.sla_state(t)}")
    print("\nHistory:")
    for h in core.get_history(conn, a.id):
        print(f"  [{h['time']}] {h['note']}")


def cmd_assign(conn, a):
    core.assign_ticket(conn, a.id, a.tech)
    print(f"Ticket #{a.id} assigned to {a.tech}")


def cmd_status(conn, a):
    core.change_status(conn, a.id, a.new_status, a.override)
    print(f"Ticket #{a.id} is now {a.new_status}")


def cmd_comment(conn, a):
    core.add_comment(conn, a.id, a.text)
    print("Comment added.")


def cmd_asset_add(conn, a):
    core.add_asset(conn, a.tag, a.name, a.site, a.rack, a.feed)
    print(f"Added asset {a.tag}")


def cmd_assets(conn, a):
    for r in core.list_assets(conn):
        print(f"{r['asset_tag']:<12}{r['name']:<28}{r['site'] or '-':<6}"
              f"{r['rack'] or '-':<6}{r['feed'] or '-':<3}{r['active_tickets']} active")


def build_parser():
    p = argparse.ArgumentParser(description="Datacenter ticket system")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, func, help_):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(func=func)
        return sp

    c = add("create", cmd_create, "open a new ticket")
    c.add_argument("title")
    c.add_argument("--type", choices=core.TYPES, required=True)
    c.add_argument("--priority", choices=core.PRIORITIES, default="medium")
    c.add_argument("--site")
    c.add_argument("--rack", help="e.g. R12")
    c.add_argument("--feed", choices=core.FEEDS, help="power feed A or B")
    c.add_argument("--asset", help="asset tag; inherits its site/rack/feed")

    l = add("list", cmd_list, "list tickets")
    l.add_argument("--status", choices=list(core.TRANSITIONS))
    l.add_argument("--priority", choices=core.PRIORITIES)
    l.add_argument("--tech")
    l.add_argument("--site")
    l.add_argument("--asset")

    s = add("show", cmd_show, "ticket details and history")
    s.add_argument("id", type=int)

    a = add("assign", cmd_assign, "assign to a technician")
    a.add_argument("id", type=int)
    a.add_argument("tech")

    st = add("status", cmd_status, "change status")
    st.add_argument("id", type=int)
    st.add_argument("new_status", choices=list(core.TRANSITIONS))
    st.add_argument("--override", help="reason, if overriding a redundancy conflict")

    cm = add("comment", cmd_comment, "add a note")
    cm.add_argument("id", type=int)
    cm.add_argument("text")

    aa = add("asset-add", cmd_asset_add, "register an asset")
    aa.add_argument("tag")
    aa.add_argument("name")
    aa.add_argument("--site")
    aa.add_argument("--rack")
    aa.add_argument("--feed", choices=core.FEEDS)

    add("assets", cmd_assets, "list assets")
    return p


def main():
    args = build_parser().parse_args()
    conn = core.connect()
    try:
        args.func(conn, args)
    except core.TicketError as e:
        raise SystemExit(f"Error: {e}")


if __name__ == "__main__":
    main()

"""Flask web UI for the datacenter ticket system.

Run with:  flask --app app run --debug
"""
import os

from flask import Flask, abort, flash, g, redirect, render_template, request, url_for

import core


def create_app(db_file=core.DB_FILE):
    app = Flask(__name__)
    app.config["DB_FILE"] = db_file
    app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
    app.jinja_env.globals.update(
        sla_state=core.sla_state, TYPES=core.TYPES,
        PRIORITIES=core.PRIORITIES, FEEDS=core.FEEDS)

    # One database connection per request, closed automatically afterwards
    def db():
        if "db" not in g:
            g.db = core.connect(app.config["DB_FILE"])
        return g.db

    @app.teardown_appcontext
    def close_db(_exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    def act(ticket_id, fn, *args, ok):
        """Run one ticket action, flash the outcome, return to the ticket page."""
        try:
            fn(db(), ticket_id, *args)
            flash(ok)
        except core.TicketError as e:
            flash(str(e), "error")
        return redirect(url_for("ticket", ticket_id=ticket_id))

    # ----- Dashboard -----
    @app.get("/")
    def index():
        keys = ("status", "priority", "tech", "site", "asset_tag")
        filters = {k: request.args.get(k) or None for k in keys}
        return render_template("index.html", f=filters, statuses=list(core.TRANSITIONS),
                               tickets=core.list_tickets(db(), **filters))

    # ----- Create -----
    @app.route("/new", methods=["GET", "POST"])
    def new():
        if request.method == "POST":
            form = request.form
            try:
                tid = core.create_ticket(
                    db(), form.get("title"), form.get("type"), form.get("priority"),
                    form.get("site"), form.get("rack"), form.get("feed"),
                    form.get("asset_tag"))
                flash(f"Created ticket #{tid}")
                return redirect(url_for("ticket", ticket_id=tid))
            except core.TicketError as e:
                flash(str(e), "error")
        return render_template("new.html", assets=core.list_assets(db()))

    # ----- Ticket detail and actions -----
    @app.get("/ticket/<int:ticket_id>")
    def ticket(ticket_id):
        try:
            t = core.get_ticket(db(), ticket_id)
        except core.TicketError:
            abort(404)
        return render_template(
            "ticket.html", t=t, history=core.get_history(db(), ticket_id),
            next_statuses=core.TRANSITIONS[t["status"]],
            conflicts=core.find_conflicts(db(), t))

    @app.post("/ticket/<int:ticket_id>/assign")
    def assign(ticket_id):
        return act(ticket_id, core.assign_ticket, request.form.get("tech"), ok="Assigned.")

    @app.post("/ticket/<int:ticket_id>/status")
    def status(ticket_id):
        return act(ticket_id, core.change_status, request.form.get("new_status"),
                   request.form.get("override_reason"), ok="Status updated.")

    @app.post("/ticket/<int:ticket_id>/comment")
    def comment(ticket_id):
        return act(ticket_id, core.add_comment, request.form.get("text"), ok="Comment added.")

    # ----- Assets -----
    @app.route("/assets", methods=["GET", "POST"])
    def assets():
        if request.method == "POST":
            form = request.form
            try:
                core.add_asset(db(), form.get("asset_tag"), form.get("name"),
                               form.get("site"), form.get("rack"), form.get("feed"))
                flash("Asset added.")
            except core.TicketError as e:
                flash(str(e), "error")
        return render_template("assets.html", assets=core.list_assets(db()))

    return app


if __name__ == "__main__":
    create_app().run()

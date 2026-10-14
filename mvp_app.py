"""Exception-management MVP on mock records only. No database

    pip install flask
    python mvp_app.py          then open http://127.0.0.1:5000

Records use the Schema v1 field names, so the logic can move to MongoDB later.
Everything lives in memory and resets when you restart or click "Reset data".
Detection runs once at startup, so every role has exceptions to look at straight away.
"""
from datetime import datetime

from flask import Flask, abort, flash, redirect, render_template_string, request, session, url_for

NOW = datetime(2026, 10, 12, 9, 0)          # fixed clock so the mock dates stay meaningful
OPS, INV, CS, ADM = ("Operations Manager", "Inventory / Warehouse Staff",
                     "Customer Service Staff", "System Administrator")
USERS = {"ops": ("Dana Cho", OPS), "inventory": ("Kai Wong", INV),
         "service": ("Mina Lau", CS), "admin": ("Raj Patel", ADM)}
TYPES = ("Low Inventory", "Delayed Order", "Data Quality")
STATUSES = ("Open", "Assigned", "In Progress", "Resolved", "Escalated")
NEXT = {"Open": ("Assigned", "Escalated"), "Assigned": ("In Progress", "Escalated"),
        "In Progress": ("Resolved", "Escalated"), "Escalated": ("Assigned", "In Progress"),
        "Resolved": ()}
ORDER_STATUSES = ("Pending", "Departed", "Customs On Hold", "Complete")
OPEN_ORDER = ("Pending", "Customs On Hold")
RANK = {"High": 0, "Medium": 1, "Low": 2}
STATE = {}


class RuleError(Exception):
    pass


def seed():
    d = lambda day: datetime(2026, 10, day)
    o = lambda n, c, s, due, items: {"order_number": n, "customer_name": c, "status": s,
                                     "expected_delivery_date": due, "items": items}
    STATE["inventory"] = [
        {"sku": "SKU-101", "product_name": "Rice 5 kg", "current_stock": 140, "minimum_stock_level": 50},
        {"sku": "SKU-102", "product_name": "Soy sauce 1 L", "current_stock": 18, "minimum_stock_level": 40},
        {"sku": "SKU-103", "product_name": "Egg noodles", "current_stock": 22, "minimum_stock_level": 30},
        {"sku": "SKU-104", "product_name": "Oolong tea", "current_stock": 200, "minimum_stock_level": 60},
        {"sku": "SKU-105", "product_name": "Cooking oil 2 L", "current_stock": 75, "minimum_stock_level": 40},
        {"sku": "SKU-106", "product_name": "Cane sugar", "current_stock": 9, "minimum_stock_level": 25}]
    STATE["orders"] = [
        o("O-2001", "Harbour Mart", "Pending", d(9), [{"sku": "SKU-101", "quantity": 120}]),
        o("O-2002", "Kowloon Retail", "Departed", d(15), [{"sku": "SKU-102", "quantity": 80}]),
        o("O-2003", "Lantau Foods", "Customs On Hold", d(10), [{"sku": "SKU-103", "quantity": 40}]),
        o("O-2004", "Pearl Trading", "Pending", d(13), [{"sku": "SKU-106", "quantity": 60}]),
        o("O-2005", "Sai Kung Goods", "Pending", d(14), [{"sku": "SKU-999", "quantity": 25}]),
        o("O-2006", "", "Pending", d(15), [{"sku": "SKU-105", "quantity": 30}]),
        o("O-2007", "Wan Chai Deli", "Complete", d(5), [{"sku": "SKU-102", "quantity": 30}]),
        o("O-2008", "Central Pantry", "Pending", d(14), [{"sku": "SKU-103", "quantity": 70}])]
    STATE["exceptions"] = []


seed()


# ------------------------------------------------------------------ rules
def _new(etype, key, prio, impact, action, order=None, sku=None):
    return {"exception_type": etype, "dedupe_key": key, "priority": prio, "status": "Open",
            "impact_description": impact, "suggested_action": action,
            "related_order_id": order, "related_inventory_item_id": sku,
            "responsible_user": None, "created_at": NOW,
            "follow_ups": [{"at": NOW, "by": "System", "from_status": None,
                            "to_status": "Open", "note": "Detected by rule check"}]}


def detect(now=NOW):
    inv, orders = STATE["inventory"], STATE["orders"]
    skus, demand, found = {i["sku"] for i in inv}, {}, []
    for o in orders:
        if o["status"] in OPEN_ORDER:
            for it in o["items"]:
                demand.setdefault(it["sku"], []).append((o["order_number"], it["quantity"]))
    for i in inv:
        cur, mn = i["current_stock"], i["minimum_stock_level"]
        if cur < mn:
            hit = demand.get(i["sku"], [])
            need = sum(q for _, q in hit)
            who = (f"{len(hit)} open order(s) need {need} units: {', '.join(n for n, _ in hit)}."
                   if hit else "No open orders need it right now.")
            found.append(_new("Low Inventory", f"low:{i['sku']}", "High" if cur <= mn / 2 else "Medium",
                              f"{i['product_name']} has {cur} in stock against a minimum of {mn}. {who}",
                              f"Restock at least {mn - cur} units to reach the minimum level.",
                              sku=i["sku"]))
    for o in orders:
        n = o["order_number"]
        if o["status"] != "Complete" and o["expected_delivery_date"] < now:
            days = (now - o["expected_delivery_date"]).total_seconds() / 86400
            lines = ", ".join(f"{it['quantity']} x {it['sku']}" for it in o["items"])
            found.append(_new(
                "Delayed Order", f"delay:{n}", "High" if days > 3 else "Medium",
                f"Order {n} for {o['customer_name'] or 'an unnamed customer'} is {days:.1f} days past "
                f"its expected delivery date (status: {o['status']}). It contains {lines}.",
                "Contact the customs broker and give the customer a new date."
                if o["status"] == "Customs On Hold" else
                "Confirm the shipment's location with the carrier and update the customer.", order=n))
        problems = []
        if not o["customer_name"].strip():
            problems.append(("customer", "is missing a customer name"))
        if not o["items"]:
            problems.append(("items", "has no items"))
        bad = [it["sku"] for it in o["items"] if it["sku"] not in skus or it["quantity"] <= 0]
        if bad:
            problems.append(("lines", f"has invalid item lines or an unknown SKU: {', '.join(bad)}"))
        for code, text in problems:
            found.append(_new("Data Quality", f"dq:{n}:{code}", "Medium", f"Order {n} {text}.",
                              "Correct the record, then run detection again.", order=n))
    created = 0
    for e in found:
        if not any(x["dedupe_key"] == e["dedupe_key"] and x["status"] != "Resolved"
                   for x in STATE["exceptions"]):
            e["id"] = len(STATE["exceptions"]) + 1
            STATE["exceptions"].append(e)
            created += 1
    return created


def visible(e, role):
    if role == OPS:
        return True
    if role == INV:
        return e["related_inventory_item_id"] is not None
    if role == CS:
        return e["related_order_id"] is not None
    return False


def listing(role, status=None, etype=None):
    rows = [e for e in STATE["exceptions"] if visible(e, role)
            and (not status or e["status"] == status) and (not etype or e["exception_type"] == etype)]
    return sorted(rows, key=lambda e: (e["status"] == "Resolved", RANK[e["priority"]], e["id"]))


def transition(e, to, actor, owner=None, note=""):
    if to not in NEXT[e["status"]]:
        raise RuleError(f"Cannot move from {e['status']} to {to}.")
    note = (note or "").strip()
    who = owner or e["responsible_user"]
    if to == "Escalated":
        who = "ops"
    elif to in ("Assigned", "In Progress") and not who:
        raise RuleError("Choose a responsible user first.")
    if to == "Resolved" and not note:
        raise RuleError("Add resolution notes before resolving.")
    e["follow_ups"].append({"at": NOW, "by": USERS[actor][0], "from_status": e["status"],
                            "to_status": to, "note": note})
    e["status"], e["responsible_user"] = to, who


# ------------------------------------------------------------------ pages
BASE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Exception desk</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head><body class="container py-4">
<nav class="d-flex flex-wrap gap-3 align-items-center mb-3 border-bottom pb-2">
 <strong>Exception desk</strong><a href="{{ url_for('index') }}">Exceptions</a>
 <a href="{{ url_for('data') }}">Source data</a>
 <span class="ms-auto small text-muted">Signed in as {{ name }} ({{ role }})</span>
 {% for u, (n, r) in users.items() %}<a class="small" href="{{ url_for('login', username=u) }}">{{ u }}</a>{% endfor %}
</nav>
{% for c, m in get_flashed_messages(with_categories=true) %}<div class="alert alert-{{ c }}">{{ m }}</div>{% endfor %}
{{ body|safe }}</body></html>"""

LIST = """
<div class="d-flex justify-content-between align-items-center mb-3"><h1 class="h3 mb-0">Exceptions</h1>
{% if role == OPS %}<form method="post" action="{{ url_for('run') }}"><button class="btn btn-primary">Run detection</button></form>{% endif %}</div>
<div class="row g-2 mb-3">{% for s in STATUSES %}<div class="col-6 col-md"><div class="border rounded p-2">
<div class="fs-4 fw-semibold">{{ counts[s] }}</div><div class="small text-muted">{{ s }}</div></div></div>{% endfor %}</div>
<form class="row g-2 mb-3"><div class="col-auto"><select name="status" class="form-select" aria-label="Status"><option value="">All statuses</option>
{% for s in STATUSES %}<option {{ 'selected' if s == sel_s }}>{{ s }}</option>{% endfor %}</select></div>
<div class="col-auto"><select name="type" class="form-select" aria-label="Type"><option value="">All types</option>
{% for t in TYPES %}<option {{ 'selected' if t == sel_t }}>{{ t }}</option>{% endfor %}</select></div>
<div class="col-auto"><button class="btn btn-outline-secondary">Filter</button></div></form>
{% if rows %}<div class="table-responsive"><table class="table table-hover align-middle"><thead><tr>
<th>Type</th><th>Related</th><th>Priority</th><th>Status</th><th>Responsible</th></tr></thead><tbody>
{% for e in rows %}<tr><td><a href="{{ url_for('detail', exc_id=e.id) }}">{{ e.exception_type }}</a></td>
<td>{{ e.related_order_id or e.related_inventory_item_id }}</td>
<td><span class="badge text-bg-{{ {'High':'danger','Medium':'warning','Low':'secondary'}[e.priority] }}">{{ e.priority }}</span></td>
<td>{{ e.status }}</td><td>{{ users[e.responsible_user][0] if e.responsible_user else 'Unassigned' }}</td></tr>{% endfor %}
</tbody></table></div>
{% elif role == ADM %}<p class="text-muted">The System Administrator role manages accounts and has no access to exceptions. Switch to ops, inventory or service using the links at the top.</p>
{% else %}<p class="text-muted">No exceptions match this view.{% if role == OPS %} Click Run detection to check the records.{% endif %}</p>{% endif %}"""

DETAIL = """
<a class="small" href="{{ url_for('index') }}">Back to exceptions</a>
<h1 class="h3 mt-2">{{ e.exception_type }}: {{ e.related_order_id or e.related_inventory_item_id }}</h1>
<p><span class="badge text-bg-{{ {'High':'danger','Medium':'warning','Low':'secondary'}[e.priority] }}">{{ e.priority }}</span>
<span class="badge text-bg-light border">{{ e.status }}</span>
<span class="text-muted ms-2">Responsible: {{ users[e.responsible_user][0] if e.responsible_user else 'Unassigned' }}</span></p>
<div class="border-start border-3 ps-3 mb-3"><p class="mb-1"><strong>What is affected.</strong> {{ e.impact_description }}</p>
<p class="mb-0"><strong>Suggested action.</strong> {{ e.suggested_action }}</p></div>
{% if NEXT[e.status] %}<form method="post" action="{{ url_for('change', exc_id=e.id) }}" class="mb-4">
<label class="form-label" for="owner">Responsible user</label>
<select id="owner" name="owner" class="form-select mb-2"><option value="">Keep current</option>
{% for u, (n, r) in users.items() if r != ADM %}<option value="{{ u }}">{{ n }} ({{ r }})</option>{% endfor %}</select>
<label class="form-label" for="note">Note (required to resolve)</label>
<textarea id="note" name="note" class="form-control mb-2" rows="2"></textarea>
{% for s in NEXT[e.status] %}<button class="btn btn-{{ 'outline-danger' if s == 'Escalated' else 'primary' }}" name="to" value="{{ s }}">{{ 'Escalate' if s == 'Escalated' else 'Mark ' ~ s.lower() }}</button> {% endfor %}
</form>{% else %}<p class="text-muted">Resolved. No further changes.</p>{% endif %}
<h2 class="h5">Follow-up history</h2><ul class="list-group">{% for f in e.follow_ups|reverse %}
<li class="list-group-item"><span class="small text-muted">{{ f.at.strftime('%d %b %Y %H:%M') }} by {{ f.by }}</span><br>
{% if f.from_status %}{{ f.from_status }} to {{ f.to_status }}{% if f.note %}: {{ f.note }}{% endif %}{% else %}{{ f.note }}{% endif %}</li>{% endfor %}</ul>"""

DATA = """
<h1 class="h3">Source data (mock)</h1><p class="text-muted">Change a value, then run detection to see how the rules react.</p>
<h2 class="h5">Inventory</h2><table class="table table-sm align-middle"><thead><tr><th>SKU</th><th>Item</th><th>Minimum</th><th>On hand</th></tr></thead><tbody>
{% for i in inv %}<tr><td>{{ i.sku }}</td><td>{{ i.product_name }}</td><td>{{ i.minimum_stock_level }}</td><td>
<form method="post" action="{{ url_for('set_stock') }}" class="d-flex gap-2"><input type="hidden" name="sku" value="{{ i.sku }}">
<input type="number" min="0" name="qty" value="{{ i.current_stock }}" class="form-control form-control-sm" style="width:90px" aria-label="Stock for {{ i.sku }}">
<button class="btn btn-sm btn-outline-secondary">Save</button></form></td></tr>{% endfor %}</tbody></table>
<h2 class="h5">Orders</h2><table class="table table-sm align-middle"><thead><tr><th>Order</th><th>Customer</th><th>Due</th><th>Items</th><th>Status</th></tr></thead><tbody>
{% for o in orders %}<tr><td>{{ o.order_number }}</td><td>{{ o.customer_name or '(missing)' }}</td><td>{{ o.expected_delivery_date.strftime('%d %b') }}</td>
<td>{% for it in o['items'] %}{{ it.quantity }} x {{ it.sku }} {% endfor %}</td><td>
<form method="post" action="{{ url_for('set_status') }}" class="d-flex gap-2"><input type="hidden" name="number" value="{{ o.order_number }}">
<select name="status" class="form-select form-select-sm" style="width:160px" aria-label="Status for {{ o.order_number }}">
{% for s in ORDER_STATUSES %}<option {{ 'selected' if s == o.status }}>{{ s }}</option>{% endfor %}</select>
<button class="btn btn-sm btn-outline-secondary">Save</button></form></td></tr>{% endfor %}</tbody></table>
<form method="post" action="{{ url_for('reset') }}"><button class="btn btn-outline-danger btn-sm">Reset data</button></form>"""

app = Flask(__name__)
app.secret_key = "dev-only-change-me"
CTX = dict(OPS=OPS, TYPES=TYPES, STATUSES=STATUSES, NEXT=NEXT, ORDER_STATUSES=ORDER_STATUSES,
           ADM=ADM, users=USERS)


def me():
    username = session.get("user", "ops")
    return username, USERS[username][0], USERS[username][1]


def page(tpl, **ctx):
    _, name, role = me()
    ctx = {**CTX, "role": role, "name": name, **ctx}
    return render_template_string(BASE, body=render_template_string(tpl, **ctx), **ctx)


@app.route("/")
def index():
    _, _, role = me()
    s, t = request.args.get("status") or None, request.args.get("type") or None
    if s not in (None, *STATUSES) or t not in (None, *TYPES):
        abort(400)
    counts = {x: sum(1 for e in listing(role) if e["status"] == x) for x in STATUSES}
    return page(LIST, rows=listing(role, s, t), counts=counts, sel_s=s, sel_t=t)


@app.route("/as/<username>")
def login(username):
    if username not in USERS:
        abort(404)
    session["user"] = username
    return redirect(url_for("index"))


@app.route("/run", methods=["POST"])
def run():
    if me()[2] != OPS:
        abort(403)
    flash(f"Detection finished: {detect()} new exception(s).", "success")
    return redirect(url_for("index"))


@app.route("/exceptions/<int:exc_id>")
def detail(exc_id):
    e = next((x for x in STATE["exceptions"] if x["id"] == exc_id and visible(x, me()[2])), None)
    if e is None:
        abort(404)
    return page(DETAIL, e=e)


@app.route("/exceptions/<int:exc_id>/change", methods=["POST"])
def change(exc_id):
    username, _, role = me()
    e = next((x for x in STATE["exceptions"] if x["id"] == exc_id and visible(x, role)), None)
    if e is None:
        abort(404)
    owner = request.form.get("owner") or None
    try:
        if owner is not None and (owner not in USERS or USERS[owner][1] == ADM):
            raise RuleError("That user cannot own exceptions.")
        transition(e, request.form.get("to", ""), username, owner, request.form.get("note"))
        flash("Exception updated.", "success")
    except RuleError as err:
        flash(str(err), "danger")
    return redirect(url_for("detail", exc_id=exc_id))


@app.route("/data")
def data():
    return page(DATA, inv=STATE["inventory"], orders=STATE["orders"])


@app.route("/data/stock", methods=["POST"])
def set_stock():
    item = next((i for i in STATE["inventory"] if i["sku"] == request.form.get("sku")), None)
    qty = request.form.get("qty", "")
    if item is None or not qty.isdigit():
        flash("Enter a whole number of units, 0 or more.", "danger")
    else:
        item["current_stock"] = int(qty)
        flash(f"{item['sku']} stock set to {qty}.", "success")
    return redirect(url_for("data"))


@app.route("/data/order", methods=["POST"])
def set_status():
    order = next((o for o in STATE["orders"] if o["order_number"] == request.form.get("number")), None)
    status = request.form.get("status")
    if order is None or status not in ORDER_STATUSES:
        flash("Unknown order or status.", "danger")
    else:
        order["status"] = status
        flash(f"{order['order_number']} set to {status}.", "success")
    return redirect(url_for("data"))


@app.route("/reset", methods=["POST"])
def reset():
    seed()
    detect()
    flash("Mock data reset and detection re-run.", "success")
    return redirect(url_for("index"))


detect()        # start with the exceptions already detected so every page has something to show

if __name__ == "__main__":
    app.run(debug=False)

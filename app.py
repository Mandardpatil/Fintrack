from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
from functools import wraps
from collections import defaultdict
from calendar import monthrange
import os, json, io, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    SCHEDULER_AVAILABLE = True
except ImportError:
    SCHEDULER_AVAILABLE = False

app = Flask(__name__)
app.config['SECRET_KEY'] = 'fintrack-secret-2024'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///expense_tracker.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

EMAIL_CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'email_config.json')

def load_email_config():
    if os.path.exists(EMAIL_CONFIG_FILE):
        with open(EMAIL_CONFIG_FILE) as f:
            return json.load(f)
    return {'enabled': False, 'smtp_server': 'smtp.gmail.com', 'smtp_port': 587,
            'username': '', 'password': '', 'reminder_hour': 8}

def save_email_config(cfg):
    with open(EMAIL_CONFIG_FILE, 'w') as f:
        json.dump(cfg, f, indent=2)

# ── Models ───────────────────────────────────────────────────────────────────
class User(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80),  unique=True, nullable=False)
    email         = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role          = db.Column(db.String(20),  default='user')
    is_approved   = db.Column(db.Boolean,     default=False)
    created_at    = db.Column(db.DateTime,    default=datetime.utcnow)
    expenses = db.relationship('Expense', backref='owner',  lazy=True, cascade='all, delete-orphan')
    incomes  = db.relationship('Income',  backref='earner', lazy=True, cascade='all, delete-orphan')
    savings  = db.relationship('Saving',  backref='saver',  lazy=True, cascade='all, delete-orphan')

class Expense(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    amount      = db.Column(db.Float,   nullable=False)
    category    = db.Column(db.String(50), nullable=False)
    description = db.Column(db.String(200))
    date        = db.Column(db.Date, nullable=False, default=date.today)

class Income(db.Model):
    id      = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    amount  = db.Column(db.Float,   nullable=False)
    source  = db.Column(db.String(100))
    date    = db.Column(db.Date, nullable=False, default=date.today)

class Saving(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    amount        = db.Column(db.Float,   nullable=False)
    description   = db.Column(db.String(200))
    goal          = db.Column(db.String(100))
    target_amount = db.Column(db.Float, default=0.0)
    date          = db.Column(db.Date, nullable=False, default=date.today)

class AuditLog(db.Model):
    """Immutable audit trail — every create / update / delete is recorded."""
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    username    = db.Column(db.String(80), nullable=False)          # denormalised for history
    action      = db.Column(db.String(20), nullable=False)          # CREATE | UPDATE | DELETE
    entity_type = db.Column(db.String(30), nullable=False)          # Expense | Income | Saving
    entity_id   = db.Column(db.Integer,    nullable=True)
    old_values  = db.Column(db.Text,       nullable=True)           # JSON snapshot before change
    new_values  = db.Column(db.Text,       nullable=True)           # JSON snapshot after change
    ip_address  = db.Column(db.String(50), nullable=True)
    timestamp   = db.Column(db.DateTime,   default=datetime.utcnow, nullable=False)

    @property
    def old_dict(self):
        return json.loads(self.old_values) if self.old_values else {}

    @property
    def new_dict(self):
        return json.loads(self.new_values) if self.new_values else {}

    @property
    def diff(self):
        """Return list of (field, old, new) tuples for changed fields."""
        old, new = self.old_dict, self.new_dict
        changes = []
        for k in set(list(old.keys()) + list(new.keys())):
            ov, nv = old.get(k, '—'), new.get(k, '—')
            if str(ov) != str(nv):
                changes.append((k, ov, nv))
        return changes

CATEGORIES   = ['Food','Travel','Shopping','Bills','Entertainment','Health','Education','Hotel','Movie','Other']
SAVING_GOALS = ['Emergency Fund','Vacation','Home','Education','Retirement','Vehicle','Wedding','Gadget','Other']

# ── Audit helpers ─────────────────────────────────────────────────────────────
def _expense_snap(e):
    return {'amount': e.amount, 'category': e.category,
            'description': e.description, 'date': str(e.date)}

def _income_snap(i):
    return {'amount': i.amount, 'source': i.source, 'date': str(i.date)}

def _saving_snap(s):
    return {'amount': s.amount, 'goal': s.goal,
            'description': s.description, 'target_amount': s.target_amount, 'date': str(s.date)}

def log_action(action, entity_type, entity_id, old=None, new=None):
    """Write one immutable AuditLog row."""
    uid  = session.get('user_id', 0)
    uname = session.get('username', 'system')
    ip   = request.remote_addr
    entry = AuditLog(
        user_id=uid, username=uname, action=action,
        entity_type=entity_type, entity_id=entity_id,
        old_values=json.dumps(old) if old else None,
        new_values=json.dumps(new) if new else None,
        ip_address=ip
    )
    db.session.add(entry)
    # do NOT commit here — caller already commits after the main change

# ── Auth helpers ─────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def dec(*a, **kw):
        if 'user_id' not in session:
            flash('Please log in.', 'warning')
            return redirect(url_for('login'))
        return f(*a, **kw)
    return dec

def admin_required(f):
    @wraps(f)
    def dec(*a, **kw):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        u = User.query.get(session['user_id'])
        if not u or u.role != 'admin':
            flash('Admin only.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*a, **kw)
    return dec

# ── Period helpers ────────────────────────────────────────────────────────────
def get_date_range(period, month=None, year=None, quarter=None):
    today = date.today()
    if period == 'daily':
        return today, today
    if period == 'monthly':
        y, m = (int(x) for x in month.split('-')) if month else (today.year, today.month)
        return date(y, m, 1), date(y, m, monthrange(y, m)[1])
    if period == 'quarterly':
        y = int(year) if year else today.year
        q = int(quarter) if quarter else ((today.month - 1) // 3 + 1)
        sm = (q - 1) * 3 + 1
        em = sm + 2
        return date(y, sm, 1), date(y, em, monthrange(y, em)[1])
    if period == 'yearly':
        y = int(year) if year else today.year
        return date(y, 1, 1), date(y, 12, 31)
    return None, None   # 'all'

def period_chart(expenses, period, start, end, quarter=None):
    MN = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    if period == 'daily':
        d = defaultdict(float)
        for e in expenses: d[e.category] += e.amount
        return list(d.keys()), [round(v,2) for v in d.values()], 'doughnut'
    if period == 'monthly':
        d = defaultdict(float)
        for e in expenses: d[e.date.day] += e.amount
        days = range(1, monthrange(start.year, start.month)[1] + 1)
        return [str(x) for x in days], [round(d.get(x,0),2) for x in days], 'bar'
    if period == 'quarterly':
        q = quarter or 1
        sm = (q-1)*3+1
        d = defaultdict(float)
        for e in expenses: d[e.date.month] += e.amount
        months = range(sm, sm+3)
        return [MN[m-1] for m in months], [round(d.get(m,0),2) for m in months], 'bar'
    if period == 'yearly':
        d = defaultdict(float)
        for e in expenses: d[e.date.month] += e.amount
        return MN, [round(d.get(i+1,0),2) for i in range(12)], 'bar'
    # all
    d = defaultdict(float)
    for e in expenses: d[e.date.year] += e.amount
    labels = sorted(str(y) for y in d)
    return labels, [round(d.get(int(l),0),2) for l in labels], 'bar'

def dash_data(user_id, period='all', month=None, year=None, quarter=None, member_id='family'):
    from datetime import date as dt
    today = dt.today()
    s, e = get_date_range(period, month, year, quarter)

    # ── Resolve which user-ids to aggregate ──────────────────────────────────
    all_members = User.query.filter_by(is_approved=True).all()
    is_family   = (member_id == 'family')

    if is_family:
        target_ids = [m.id for m in all_members]
        view_label = '👨‍👩‍👧 Family'
    else:
        mid = int(member_id)
        target_ids = [mid]
        member_obj = User.query.get(mid)
        view_label = f'👤 {member_obj.username}' if member_obj else '👤 Member'

    # ── Core queries (period-filtered) ───────────────────────────────────────
    eq = Expense.query.filter(Expense.user_id.in_(target_ids))
    iq = Income.query.filter(Income.user_id.in_(target_ids))
    sq = Saving.query.filter(Saving.user_id.in_(target_ids))
    if s:
        eq = eq.filter(Expense.date >= s, Expense.date <= e)
        iq = iq.filter(Income.date >= s, Income.date <= e)
        sq = sq.filter(Saving.date >= s, Saving.date <= e)
    exps = eq.order_by(Expense.date.desc()).all()
    incs = iq.order_by(Income.date.desc()).all()
    savs = sq.order_by(Saving.date.desc()).all()

    ti  = sum(i.amount for i in incs)
    te  = sum(x.amount for x in exps)
    ts  = sum(x.amount for x in savs)
    bal = ti - te - ts

    # All-time pool (no period filter) for sparklines
    all_exps = Expense.query.filter(Expense.user_id.in_(target_ids)).all()
    today_exp = sum(x.amount for x in all_exps if x.date == today)
    ms = dt(today.year, today.month, 1)
    month_exp = sum(x.amount for x in all_exps if ms <= x.date <= today)

    # ── Chart 1: period trend ─────────────────────────────────────────────────
    q_int = int(quarter) if quarter else None
    cl, cd, ct = period_chart(exps, period, s, e, q_int)

    # ── Chart 2: category doughnut ────────────────────────────────────────────
    catd = defaultdict(float)
    for x in exps: catd[x.category] += x.amount
    cat_labels_raw = list(catd.keys())
    cat_data_raw   = [round(v, 2) for v in catd.values()]

    # ── Chart 3: last 12 months ───────────────────────────────────────────────
    MN = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    monthly12 = defaultdict(float)
    for x in all_exps:
        monthly12[x.date.strftime('%b %Y')] += x.amount
    keys12, cur = [], today.replace(day=1)
    for _ in range(12):
        keys12.insert(0, cur.strftime('%b %Y'))
        cur = cur.replace(month=12, year=cur.year-1) if cur.month == 1 else cur.replace(month=cur.month-1)
    m12_labels = [k.split()[0] + " '" + k.split()[1][2:] for k in keys12]
    m12_data   = [round(monthly12.get(k, 0), 2) for k in keys12]

    # ── Chart 4: Income vs Expense vs Savings (current year, monthly) ─────────
    cy = today.year
    cy_incs = Income.query.filter(Income.user_id.in_(target_ids),
                Income.date >= dt(cy,1,1), Income.date <= dt(cy,12,31)).all()
    cy_savs = Saving.query.filter(Saving.user_id.in_(target_ids),
                Saving.date >= dt(cy,1,1), Saving.date <= dt(cy,12,31)).all()
    cy_exps  = [x for x in all_exps if x.date.year == cy]
    cmp_inc  = [0.0]*12; cmp_exp = [0.0]*12; cmp_sav = [0.0]*12
    for x in cy_exps:  cmp_exp[x.date.month-1] = round(cmp_exp[x.date.month-1] + x.amount, 2)
    for x in cy_incs:  cmp_inc[x.date.month-1] = round(cmp_inc[x.date.month-1] + x.amount, 2)
    for x in cy_savs:  cmp_sav[x.date.month-1] = round(cmp_sav[x.date.month-1] + x.amount, 2)

    # ── Chart 5: all-time category bar ────────────────────────────────────────
    atcat = defaultdict(float)
    for x in all_exps: atcat[x.category] += x.amount
    at_sorted     = sorted(atcat.items(), key=lambda kv: kv[1], reverse=True)
    at_cat_labels = [k for k, _ in at_sorted]
    at_cat_data   = [round(v, 2) for _, v in at_sorted]

    # ── Chart 6: Member-wise expense breakdown (family view only) ─────────────
    member_names = []
    member_totals = []
    member_today  = []
    member_month  = []
    if is_family:
        for m in all_members:
            m_exps = [x for x in all_exps if x.user_id == m.id]
            m_period_exps = [x for x in exps if x.user_id == m.id]
            member_names.append(m.username)
            member_totals.append(round(sum(x.amount for x in m_period_exps), 2))
            member_today.append(round(sum(x.amount for x in m_exps if x.date == today), 2))
            member_month.append(round(sum(x.amount for x in m_exps if ms <= x.date <= today), 2))

    return dict(
        total_income=ti, total_expense=te, total_savings=ts, balance=bal,
        today_exp=today_exp, month_exp=month_exp,
        expenses=exps, incomes=incs, savings=savs, recent=exps[:10],
        now=today, is_family=is_family, view_label=view_label,
        all_members=all_members,
        member_id=str(member_id),
        # chart 1
        chart_labels=json.dumps(cl), chart_data=json.dumps(cd), chart_type=ct,
        chart_data_raw=cd,
        # chart 2
        cat_labels=json.dumps(cat_labels_raw), cat_data=json.dumps(cat_data_raw),
        cat_data_raw=cat_data_raw,
        # chart 3
        monthly12_labels=json.dumps(m12_labels), monthly12_data=json.dumps(m12_data),
        # chart 4
        compare_labels=json.dumps(MN), compare_income=json.dumps(cmp_inc),
        compare_expense=json.dumps(cmp_exp), compare_savings=json.dumps(cmp_sav),
        # chart 5
        alltime_cat_labels=json.dumps(at_cat_labels), alltime_cat_data=json.dumps(at_cat_data),
        alltime_cat_labels_raw=at_cat_labels,
        # chart 6 (family breakdown)
        member_names=json.dumps(member_names),
        member_totals=json.dumps(member_totals),
        member_today=json.dumps(member_today),
        member_month=json.dumps(member_month),
        member_totals_raw=member_totals,
        member_today_raw=member_today,
        member_month_raw=member_month,
        has_member_chart=is_family and len(all_members) > 1,
        categories=CATEGORIES, saving_goals=SAVING_GOALS,
        period=period,
        month=month or today.strftime('%Y-%m'),
        year=year or str(today.year),
        quarter=quarter or str((today.month - 1) // 3 + 1)
    )



# ── Email reminder ───────────────────────────────────────────────────────────
def send_reminder_email(user, cfg):
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = '💰 FinTrack Daily Reminder – Log your expenses!'
        msg['From']    = f"FinTrack <{cfg['username']}>"
        msg['To']      = user.email
        html = f"""
        <div style="font-family:Inter,sans-serif;max-width:480px;margin:auto;background:#1a1927;color:#e8e6ff;padding:32px;border-radius:16px;">
          <h2 style="color:#6c63ff;">💰 FinTrack Daily Reminder</h2>
          <p>Hi <strong>{user.username}</strong>,</p>
          <p>Don't forget to log your expenses for today! Staying on top of your finances daily helps you save more.</p>
          <a href="http://127.0.0.1:5000/expenses/add" style="display:inline-block;background:#6c63ff;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;margin-top:16px;">Log Today's Expenses →</a>
          <p style="color:#9796b8;font-size:12px;margin-top:24px;">FinTrack – Smart Expense Tracker</p>
        </div>"""
        msg.attach(MIMEText(html, 'html'))
        with smtplib.SMTP(cfg['smtp_server'], cfg['smtp_port']) as s:
            s.starttls()
            s.login(cfg['username'], cfg['password'])
            s.send_message(msg)
    except Exception as ex:
        print(f"[FinTrack] Email error for {user.email}: {ex}")

def daily_reminders():
    cfg = load_email_config()
    if not cfg.get('enabled') or not cfg.get('username'):
        return
    with app.app_context():
        users = User.query.filter_by(is_approved=True).all()
        today = date.today()
        for u in users:
            has_today = Expense.query.filter_by(user_id=u.id).filter(Expense.date==today).first()
            if not has_today:
                send_reminder_email(u, cfg)

# ── Scheduler (only in main process) ─────────────────────────────────────────
if SCHEDULER_AVAILABLE and (not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true'):
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(daily_reminders, 'cron', hour=8, minute=0, timezone='Asia/Kolkata')
    scheduler.start()

# ── Routes: index / auth ──────────────────────────────────────────────────────
@app.route('/')
def index():
    return redirect(url_for('dashboard') if 'user_id' in session else url_for('login'))

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        un = request.form.get('username','').strip()
        em = request.form.get('email','').strip().lower()
        pw = request.form.get('password','').strip()
        if not (un and em and pw):
            flash('All fields required.', 'danger')
            return render_template('register.html')
        if User.query.filter_by(username=un).first():
            flash('Username taken.', 'danger'); return render_template('register.html')
        if User.query.filter_by(email=em).first():
            flash('Email already registered.', 'danger'); return render_template('register.html')
        first = User.query.count() == 0
        u = User(username=un, email=em, password_hash=generate_password_hash(pw),
                 role='admin' if first else 'user', is_approved=first)
        db.session.add(u); db.session.commit()
        flash('Admin account created! Log in now.' if first else 'Registered! Awaiting admin approval.', 'success' if first else 'info')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        em = request.form.get('email','').strip().lower()
        pw = request.form.get('password','').strip()
        u  = User.query.filter_by(email=em).first()
        if not u or not check_password_hash(u.password_hash, pw):
            flash('Invalid credentials.', 'danger'); return render_template('login.html')
        if not u.is_approved:
            flash('Account pending admin approval.', 'warning'); return render_template('login.html')
        session.update(user_id=u.id, username=u.username, role=u.role)
        flash(f'Welcome, {u.username}!', 'success')
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear(); flash('Logged out.', 'info'); return redirect(url_for('login'))

# ── Dashboard ─────────────────────────────────────────────────────────────────
@app.route('/dashboard')
@login_required
def dashboard():
    period    = request.args.get('period', 'all')
    month     = request.args.get('month')
    year      = request.args.get('year')
    quarter   = request.args.get('quarter')
    member_id = request.args.get('member_id', 'family')
    data      = dash_data(session['user_id'], period, month, year, quarter, member_id)
    return render_template('dashboard.html', **data)

# ── Expenses ──────────────────────────────────────────────────────────────────
@app.route('/expenses')
@login_required
def expenses():
    uid  = session['user_id']
    cat  = request.args.get('category','')
    strt = request.args.get('start','')
    end  = request.args.get('end','')
    srch = request.args.get('search','')
    q = Expense.query.filter_by(user_id=uid)
    if cat:  q = q.filter_by(category=cat)
    if strt: q = q.filter(Expense.date >= datetime.strptime(strt,'%Y-%m-%d').date())
    if end:  q = q.filter(Expense.date <= datetime.strptime(end,'%Y-%m-%d').date())
    if srch: q = q.filter(Expense.description.ilike(f'%{srch}%'))
    return render_template('expenses.html', expenses=q.order_by(Expense.date.desc()).all(),
                           categories=CATEGORIES, category=cat, start=strt, end=end, search=srch)

@app.route('/expenses/add', methods=['GET','POST'])
@login_required
def add_expense():
    if request.method == 'POST':
        try:
            e = Expense(user_id=session['user_id'], amount=float(request.form['amount']),
                        category=request.form['category'], description=request.form.get('description',''),
                        date=datetime.strptime(request.form['date'],'%Y-%m-%d').date())
            db.session.add(e)
            db.session.flush()   # get e.id before commit
            log_action('CREATE', 'Expense', e.id, new=_expense_snap(e))
            db.session.commit()
            flash('Expense added!', 'success')
        except Exception as ex: flash(str(ex), 'danger')
        return redirect(url_for('expenses'))
    return render_template('add_expense.html', categories=CATEGORIES, today=date.today().strftime('%Y-%m-%d'))

@app.route('/expenses/edit/<int:eid>', methods=['GET','POST'])
@login_required
def edit_expense(eid):
    e = Expense.query.filter_by(id=eid, user_id=session['user_id']).first_or_404()
    if request.method == 'POST':
        old = _expense_snap(e)
        e.amount=float(request.form['amount']); e.category=request.form['category']
        e.description=request.form.get('description','')
        e.date=datetime.strptime(request.form['date'],'%Y-%m-%d').date()
        log_action('UPDATE', 'Expense', e.id, old=old, new=_expense_snap(e))
        db.session.commit(); flash('Updated!', 'success')
        return redirect(url_for('expenses'))
    return render_template('edit_expense.html', expense=e, categories=CATEGORIES)

@app.route('/expenses/delete/<int:eid>', methods=['POST'])
@login_required
def delete_expense(eid):
    e = Expense.query.filter_by(id=eid, user_id=session['user_id']).first_or_404()
    log_action('DELETE', 'Expense', e.id, old=_expense_snap(e))
    db.session.delete(e); db.session.commit(); flash('Deleted.', 'info')
    return redirect(url_for('expenses'))

# ── Income ────────────────────────────────────────────────────────────────────
@app.route('/income')
@login_required
def income():
    incs = Income.query.filter_by(user_id=session['user_id']).order_by(Income.date.desc()).all()
    return render_template('income.html', incomes=incs)

@app.route('/income/add', methods=['GET','POST'])
@login_required
def add_income():
    if request.method == 'POST':
        try:
            i = Income(user_id=session['user_id'], amount=float(request.form['amount']),
                       source=request.form.get('source',''),
                       date=datetime.strptime(request.form['date'],'%Y-%m-%d').date())
            db.session.add(i)
            db.session.flush()
            log_action('CREATE', 'Income', i.id, new=_income_snap(i))
            db.session.commit(); flash('Income added!', 'success')
        except Exception as ex: flash(str(ex), 'danger')
        return redirect(url_for('income'))
    return render_template('add_income.html', today=date.today().strftime('%Y-%m-%d'))

# ── Savings ───────────────────────────────────────────────────────────────────
@app.route('/savings')
@login_required
def savings():
    savs = Saving.query.filter_by(user_id=session['user_id']).order_by(Saving.date.desc()).all()
    total = sum(s.amount for s in savs)
    by_goal = defaultdict(float)
    for s in savs: by_goal[s.goal or 'Other'] += s.amount
    return render_template('savings.html', savings=savs, total=total,
                           by_goal=dict(by_goal), saving_goals=SAVING_GOALS)

@app.route('/savings/add', methods=['GET','POST'])
@login_required
def add_saving():
    if request.method == 'POST':
        try:
            s = Saving(user_id=session['user_id'], amount=float(request.form['amount']),
                       description=request.form.get('description',''),
                       goal=request.form.get('goal','Other'),
                       target_amount=float(request.form.get('target_amount') or 0),
                       date=datetime.strptime(request.form['date'],'%Y-%m-%d').date())
            db.session.add(s)
            db.session.flush()
            log_action('CREATE', 'Saving', s.id, new=_saving_snap(s))
            db.session.commit(); flash('Saving recorded!', 'success')
        except Exception as ex: flash(str(ex), 'danger')
        return redirect(url_for('savings'))
    return render_template('add_saving.html', saving_goals=SAVING_GOALS, today=date.today().strftime('%Y-%m-%d'))

@app.route('/savings/edit/<int:sid>', methods=['GET','POST'])
@login_required
def edit_saving(sid):
    s = Saving.query.filter_by(id=sid, user_id=session['user_id']).first_or_404()
    if request.method == 'POST':
        old = _saving_snap(s)
        s.amount=float(request.form['amount']); s.description=request.form.get('description','')
        s.goal=request.form.get('goal','Other')
        s.target_amount=float(request.form.get('target_amount') or 0)
        s.date=datetime.strptime(request.form['date'],'%Y-%m-%d').date()
        log_action('UPDATE', 'Saving', s.id, old=old, new=_saving_snap(s))
        db.session.commit(); flash('Updated!', 'success')
        return redirect(url_for('savings'))
    return render_template('edit_saving.html', saving=s, saving_goals=SAVING_GOALS)

@app.route('/savings/delete/<int:sid>', methods=['POST'])
@login_required
def delete_saving(sid):
    s = Saving.query.filter_by(id=sid, user_id=session['user_id']).first_or_404()
    log_action('DELETE', 'Saving', s.id, old=_saving_snap(s))
    db.session.delete(s); db.session.commit(); flash('Saving deleted.', 'info')
    return redirect(url_for('savings'))

# ── Family (all approved users) ───────────────────────────────────────────────
@app.route('/family')
@login_required
def family():
    members = User.query.filter_by(is_approved=True).all()
    rows = []
    for m in members:
        for e in m.expenses:
            rows.append({'user': m.username, 'expense': e})
    rows.sort(key=lambda x: x['expense'].date, reverse=True)
    total = sum(r['expense'].amount for r in rows)
    by_member = {m.username: sum(e.amount for e in m.expenses) for m in members}
    return render_template('family.html', members=members, rows=rows, total=total, by_member=by_member)

# ── PDF export ─────────────────────────────────────────────────────────────────
@app.route('/export/pdf')
@login_required
def export_pdf():
    if not PDF_AVAILABLE:
        flash('ReportLab not installed.', 'danger'); return redirect(url_for('dashboard'))
    user = User.query.get(session['user_id'])
    exps = Expense.query.filter_by(user_id=user.id).order_by(Expense.date.desc()).all()
    incs = Income.query.filter_by(user_id=user.id).order_by(Income.date.desc()).all()
    savs = Saving.query.filter_by(user_id=user.id).order_by(Saving.date.desc()).all()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter)
    styles = getSampleStyleSheet()
    el = [Paragraph(f'Expense Report — {user.username}', styles['Title']),
          Paragraph(f'Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")}', styles['Normal']),
          Spacer(1,12)]
    ti = sum(i.amount for i in incs); te = sum(e.amount for e in exps); ts = sum(s.amount for s in savs)
    st = Table([['Total Income',f'₹{ti:,.2f}'],['Total Expenses',f'₹{te:,.2f}'],
                ['Total Savings',f'₹{ts:,.2f}'],['Balance',f'₹{ti-te-ts:,.2f}']], colWidths=[200,200])
    st.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#6c63ff')),
        ('TEXTCOLOR',(0,0),(-1,0),colors.white),('GRID',(0,0),(-1,-1),.5,colors.grey)]))
    el.append(st); el.append(Spacer(1,16))
    el.append(Paragraph('Expenses', styles['Heading2']))
    ed = [['Date','Category','Description','Amount']] + \
         [[str(e.date),e.category,e.description or '',f'₹{e.amount:,.2f}'] for e in exps]
    et = Table(ed, colWidths=[80,90,230,100])
    et.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#6c63ff')),
        ('TEXTCOLOR',(0,0),(-1,0),colors.white),('GRID',(0,0),(-1,-1),.25,colors.grey),
        ('FONTSIZE',(0,0),(-1,-1),9)]))
    el.append(et)
    doc.build(el); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f'report_{user.username}.pdf', mimetype='application/pdf')

# ── Admin ─────────────────────────────────────────────────────────────────────
@app.route('/admin')
@admin_required
def admin_panel():
    return render_template('admin.html', users=User.query.all(), email_cfg=load_email_config())

@app.route('/admin/approve/<int:uid>', methods=['POST'])
@admin_required
def approve_user(uid):
    u = User.query.get_or_404(uid); u.is_approved=True; db.session.commit()
    flash(f'{u.username} approved.', 'success'); return redirect(url_for('admin_panel'))

@app.route('/admin/reject/<int:uid>', methods=['POST'])
@admin_required
def reject_user(uid):
    u = User.query.get_or_404(uid); db.session.delete(u); db.session.commit()
    flash('User rejected.', 'info'); return redirect(url_for('admin_panel'))

@app.route('/admin/email', methods=['GET','POST'])
@admin_required
def email_config():
    cfg = load_email_config()
    if request.method == 'POST':
        cfg = {'enabled': 'enabled' in request.form,
               'smtp_server': request.form.get('smtp_server','smtp.gmail.com'),
               'smtp_port': int(request.form.get('smtp_port',587)),
               'username': request.form.get('username',''),
               'password': request.form.get('password',''),
               'reminder_hour': int(request.form.get('reminder_hour',8))}
        save_email_config(cfg)
        flash('Email settings saved!', 'success')
        return redirect(url_for('email_config'))
    return render_template('email_config.html', cfg=cfg)

@app.route('/admin/send-test-email')
@admin_required
def send_test_email():
    cfg = load_email_config()
    user = User.query.get(session['user_id'])
    send_reminder_email(user, cfg)
    flash('Test email sent!', 'success')
    return redirect(url_for('email_config'))

# ── Audit Trail ───────────────────────────────────────────────────────────────
@app.route('/audit')
@login_required
def audit_trail():
    uid    = session['user_id']
    etype  = request.args.get('entity', '')
    action = request.args.get('action', '')
    page   = int(request.args.get('page', 1))
    per    = 30
    q = AuditLog.query.filter_by(user_id=uid)
    if etype:  q = q.filter_by(entity_type=etype)
    if action: q = q.filter_by(action=action)
    q = q.order_by(AuditLog.timestamp.desc())
    total  = q.count()
    logs   = q.offset((page-1)*per).limit(per).all()
    pages  = (total + per - 1) // per
    return render_template('audit.html', logs=logs, total=total,
                           page=page, pages=pages, entity=etype, action=action)

@app.route('/admin/audit')
@admin_required
def admin_audit():
    etype  = request.args.get('entity', '')
    action = request.args.get('action', '')
    user_f = request.args.get('user', '')
    page   = int(request.args.get('page', 1))
    per    = 40
    q = AuditLog.query
    if etype:  q = q.filter_by(entity_type=etype)
    if action: q = q.filter_by(action=action)
    if user_f: q = q.filter(AuditLog.username.ilike(f'%{user_f}%'))
    q = q.order_by(AuditLog.timestamp.desc())
    total  = q.count()
    logs   = q.offset((page-1)*per).limit(per).all()
    pages  = (total + per - 1) // per
    users  = [u.username for u in User.query.filter_by(is_approved=True).all()]
    return render_template('admin_audit.html', logs=logs, total=total,
                           page=page, pages=pages,
                           entity=etype, action=action, user_f=user_f, users=users)

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True, port=5000)

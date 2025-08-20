# ==========================
#  app.py — GalaxyTix
#  Simple Cinema Ticketing (Admin/User)
#  Fitur: Login (admin/user), Registrasi user, CRUD film (admin only),
#         Beli tiket (jadwal, lokasi, jumlah, kursi),
#         Pembayaran (DANA: kode unik + QR; lainnya kode unik),
#         Diagram jumlah tiket per film (Chart.js di index)
#  Tema: Luar Angkasa
# ==========================

import os
import random
import string
from datetime import datetime, timedelta
from urllib.parse import quote_plus

from flask import Flask, render_template, redirect, url_for, request, flash, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user, login_required, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import text

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, 'galaxytix.db')

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-please-change')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + DB_PATH
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# ==========================
#  MODELS
# ==========================
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default='user')  # 'admin' or 'user'

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)


class Film(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    synopsis = db.Column(db.Text, default='')
    image_url = db.Column(db.String(500), default='')
    showtimes_csv = db.Column(db.Text, default='')  # "2025-08-20 19:00,2025-08-21 16:30"
    locations_csv = db.Column(db.Text, default='')  # "CGV PVJ, XXI PI"
    price = db.Column(db.Integer, default=50000)    # Harga per tiket (dalam rupiah)

    def showtimes(self):
        items = [s.strip() for s in (self.showtimes_csv or '').split(',') if s.strip()]
        return items

    def locations(self):
        items = [s.strip() for s in (self.locations_csv or '').split(',') if s.strip()]
        return items


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    film_id = db.Column(db.Integer, db.ForeignKey('film.id'), nullable=False)
    qty = db.Column(db.Integer, default=1)
    seats_csv = db.Column(db.String(200), default='')  # contoh: "A1,A2"
    showtime = db.Column(db.String(50), nullable=False)
    location = db.Column(db.String(120), nullable=False)
    payment_method = db.Column(db.String(30), nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending, paid, canceled
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # field pembayaran realistis
    total_amount = db.Column(db.Integer, default=0)
    payment_code = db.Column(db.String(32), default='')  # kode unik pembayaran
    payment_qr = db.Column(db.String(600), default='')   # url gambar QR (untuk DANA)
    payment_deadline = db.Column(db.DateTime)            # batas waktu bayar
    paid_at = db.Column(db.DateTime)                     # kapan dibayar

    user = db.relationship('User', backref='orders')
    film = db.relationship('Film', backref='orders')


# ==========================
#  LOGIN MANAGER & CONTEXT
# ==========================
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.context_processor
def inject_current_user():
    return dict(current_user=current_user)


# ==========================
#  DB UTIL (untuk upgrade kolom di sqlite lama)
# ==========================
def _ensure_columns():
    """
    Pastikan kolom baru ada di DB lama tanpa migrasi:
    - Film.price
    - Order.total_amount, payment_code, payment_qr, payment_deadline, paid_at
    """
    with db.engine.begin() as conn:
        # Cek kolom Film
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info('film')"))}
        if 'price' not in cols:
            conn.execute(text("ALTER TABLE film ADD COLUMN price INTEGER DEFAULT 50000"))

        # Cek kolom Order
        ocols = {row[1] for row in conn.execute(text("PRAGMA table_info('order')"))}
        add_cols = []
        if 'total_amount' not in ocols:
            add_cols.append("ADD COLUMN total_amount INTEGER DEFAULT 0")
        if 'payment_code' not in ocols:
            add_cols.append("ADD COLUMN payment_code VARCHAR(32) DEFAULT ''")
        if 'payment_qr' not in ocols:
            add_cols.append("ADD COLUMN payment_qr VARCHAR(600) DEFAULT ''")
        if 'payment_deadline' not in ocols:
            add_cols.append("ADD COLUMN payment_deadline DATETIME")
        if 'paid_at' not in ocols:
            add_cols.append("ADD COLUMN paid_at DATETIME")
        for stmt in add_cols:
            conn.execute(text(f"ALTER TABLE 'order' {stmt}"))


# ==========================
#  CLI SETUP / FIRST RUN
# ==========================
def init_db():
    db.create_all()
    _ensure_columns()

    # Buat admin default jika belum ada — tidak ditampilkan di UI
    admin_username = 'cristo'
    admin_password = 'cristo!@#$%'
    admin = User.query.filter_by(username=admin_username).first()
    if not admin:
        admin = User(username=admin_username, role='admin')
        admin.set_password(admin_password)
        db.session.add(admin)
        db.session.commit()

    # Seed film contoh jika kosong
    if Film.query.count() == 0:
        demo = Film(
            title='Demon Slayer: Infinity Castle — Part 1',
            synopsis='Pertarungan epik di kastil tak berujung. Siap-siap tegang!',
            image_url='https://images.unsplash.com/photo-1542273917363-3b1817f69a2d?q=80&w=1400&auto=format&fit=crop',
            showtimes_csv='2025-08-20 19:00, 2025-08-21 16:30, 2025-08-22 21:00',
            locations_csv='CGV PVJ Bandung, XXI Plaza Indonesia, Cinepolis Miko Mall',
            price=65000
        )
        db.session.add(demo)
        db.session.commit()


# ==========================
#  HELPERS
# ==========================
def admin_required():
    if not (current_user.is_authenticated and current_user.role == 'admin'):
        abort(403)

def _rand_code(n=10):
    return ''.join(random.choices(string.digits, k=n))

def _make_qr_url(data: str, size=240):
    # pakai layanan QR publik untuk demo (tanpa API key)
    payload = quote_plus(data)
    return f"https://api.qrserver.com/v1/create-qr-code/?size={size}x{size}&data={payload}"


# ==========================
#  ROUTES — AUTH
# ==========================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            flash(f'Berhasil login. Selamat datang, {user.username}!', 'success')
            return redirect(url_for('index'))
        flash('Username atau password salah.', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Kamu telah logout.', 'info')
    return redirect(url_for('login'))

@app.route('/regis', methods=['GET', 'POST'])
def regis():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if not username or not password:
            flash('Semua field wajib diisi.', 'warning')
            return render_template('regis.html')
        if User.query.filter_by(username=username).first():
            flash('Username sudah dipakai.', 'warning')
            return render_template('regis.html')
        user = User(username=username, role='user')
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash('Registrasi berhasil. Silakan login.', 'success')
        return redirect(url_for('login'))
    return render_template('regis.html')


# ==========================
#  ROUTES — MAIN / FILM / ORDER
# ==========================
@app.route('/')
@login_required
def index():
    films = Film.query.order_by(Film.id.desc()).all()

    # Data untuk Chart.js: jumlah tiket terjual per film
    labels = [f.title for f in films]
    data = []
    for f in films:
        total = sum(o.qty for o in f.orders if o.status == 'paid')
        data.append(total)

    return render_template('index.html', films=films, chart_labels=labels, chart_data=data)


@app.route('/film/add', methods=['POST'])
@login_required
def film_add():
    admin_required()
    title = request.form.get('title', '').strip()
    synopsis = request.form.get('synopsis', '').strip()
    image_url = request.form.get('image_url', '').strip()
    showtimes_csv = request.form.get('showtimes_csv', '').strip()
    locations_csv = request.form.get('locations_csv', '').strip()
    price_raw = request.form.get('price', '0').strip()

    if not title:
        flash('Judul wajib diisi.', 'warning')
        return redirect(url_for('index'))

    try:
        price = int(price_raw)
        if price <= 0:
            raise ValueError
    except Exception:
        price = 50000

    film = Film(title=title, synopsis=synopsis, image_url=image_url,
                showtimes_csv=showtimes_csv, locations_csv=locations_csv,
                price=price)
    db.session.add(film)
    db.session.commit()
    flash('Film ditambahkan.', 'success')
    return redirect(url_for('index'))


@app.route('/film/<int:film_id>/delete', methods=['POST'])
@login_required
def film_delete(film_id):
    admin_required()
    film = Film.query.get_or_404(film_id)
    db.session.delete(film)
    db.session.commit()
    flash('Film dihapus.', 'info')
    return redirect(url_for('index'))


@app.route('/beli/<int:film_id>', methods=['GET', 'POST'])
@login_required
def beli(film_id):
    film = Film.query.get_or_404(film_id)

    if request.method == 'POST':
        qty = int(request.form.get('qty', 1))
        seats_csv = request.form.get('seats_csv', '')
        showtime = request.form.get('showtime', '')
        location = request.form.get('location', '')
        payment_method = request.form.get('payment_method', '')

        if qty <= 0 or not showtime or not location or not payment_method:
            flash('Mohon lengkapi semua pilihan.', 'warning')
            return redirect(url_for('beli', film_id=film.id))

        total_amount = film.price * qty

        # Buat order + siapkan kode pembayaran (belum paid)
        order = Order(
            user_id=current_user.id,
            film_id=film.id,
            qty=qty,
            seats_csv=seats_csv,
            showtime=showtime,
            location=location,
            payment_method=payment_method,
            status='pending',
            total_amount=total_amount,
            payment_code=_rand_code(12),
            payment_deadline=datetime.utcnow() + timedelta(minutes=30),
        )

        # Kalau DANA, generate QR (simulasi)
        if payment_method == 'dana':
            payload = f"DANA|ORDER:{order.payment_code}|FILM:{film.title}|AMOUNT:{total_amount}"
            order.payment_qr = _make_qr_url(payload, size=260)

        db.session.add(order)
        db.session.commit()
        return redirect(url_for('pembayaran', order_id=order.id))

    # GET: tampilkan form pembelian
    return render_template('pembayaran.html', film=film, order=None)


@app.route('/pembayaran/<int:order_id>', methods=['GET', 'POST'])
@login_required
def pembayaran(order_id):
    order = Order.query.get_or_404(order_id)
    if order.user_id != current_user.id and current_user.role != 'admin':
        abort(403)

    # Jika order pending & belum ada QR untuk DANA, generate (edge case)
    if order.status == 'pending' and order.payment_method == 'dana' and not order.payment_qr:
        payload = f"DANA|ORDER:{order.payment_code}|FILM:{order.film.title}|AMOUNT:{order.total_amount}"
        order.payment_qr = _make_qr_url(payload, size=260)
        db.session.commit()

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'confirm':
            # Simulasi verifikasi: cek belum kadaluarsa & masih pending
            if order.status != 'pending':
                flash('Pesanan sudah tidak dalam status pembayaran.', 'warning')
                return redirect(url_for('index'))
            if order.payment_deadline and datetime.utcnow() > order.payment_deadline:
                flash('Pembayaran kadaluarsa. Silakan buat pesanan baru.', 'danger')
                order.status = 'canceled'
                db.session.commit()
                return redirect(url_for('index'))
            # tandai sebagai paid
            order.status = 'paid'
            order.paid_at = datetime.utcnow()
            db.session.commit()
            flash('Pembayaran berhasil! Tiket kamu terkonfirmasi.', 'success')
            return redirect(url_for('index'))

        elif action == 'cancel':
            order.status = 'canceled'
            db.session.commit()
            flash('Pesanan dibatalkan.', 'info')
            return redirect(url_for('index'))

    return render_template('pembayaran.html', film=order.film, order=order)


# ==========================
#  RUN
# ==========================
if __name__ == '__main__':
    with app.app_context():
        init_db()
    app.run(debug=True)


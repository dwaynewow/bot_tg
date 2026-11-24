"""Telegram Stars recurring subscriptions with server-side entitlement checks."""
import secrets
import sqlite3
import time

PERIOD = 30 * 24 * 60 * 60


class Billing:
    def __init__(self, path, stars=0):
        self.path = path
        self.stars = stars
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS invoices (
                    payload TEXT PRIMARY KEY, user INTEGER NOT NULL, amount INTEGER NOT NULL,
                    created INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS subscription_settings (user INTEGER PRIMARY KEY, canceled INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS payments (
                    charge TEXT PRIMARY KEY, user INTEGER NOT NULL, expires INTEGER NOT NULL,
                    refunded INTEGER NOT NULL DEFAULT 0
                );
            """)

    def db(self):
        return sqlite3.connect(self.path, timeout=15)

    def active_until(self, user):
        with self.db() as db:
            row = db.execute("SELECT MAX(expires) FROM payments WHERE user=? AND refunded=0",
                             (user,)).fetchone()
        return row[0] or 0

    def allowed(self, user, answers):
        family = answers.get("family", {})
        return family.get("adults", 0) + family.get("children", 0) <= 1 or self.active_until(user) > time.time()

    def invoice(self, user):
        if not 1 <= self.stars <= 10000:
            raise ValueError("Оплата пока не настроена. Обратитесь в поддержку.")
        if self.active_until(user) > time.time():
            raise ValueError("Семейная подписка уже активна.")
        payload = secrets.token_urlsafe(24)
        with self.db() as db:
            db.execute("INSERT INTO invoices VALUES(?,?,?,?)", (payload, user, self.stars, int(time.time())))
        return payload

    def valid(self, user, payload, currency, amount, renewal=False):
        with self.db() as db:
            row = db.execute("SELECT user,amount,created FROM invoices WHERE payload=?", (payload,)).fetchone()
        return bool(row and row[0] == user and currency == "XTR" and row[1] == amount
                    and (renewal or time.time() - row[2] <= 3600))

    def payment(self, user, payment):
        if (not self.valid(user, payment.invoice_payload, payment.currency, payment.total_amount, renewal=True)
                or not payment.subscription_expiration_date):
            raise ValueError("Неизвестный платёж")
        expires = int(payment.subscription_expiration_date.timestamp())
        with self.db() as db:
            inserted = db.execute("INSERT OR IGNORE INTO payments(charge,user,expires) VALUES(?,?,?)",
                       (payment.telegram_payment_charge_id, user, expires)).rowcount
            if inserted:
                db.execute("INSERT INTO subscription_settings VALUES(?,0) ON CONFLICT(user) DO UPDATE SET canceled=0", (user,))

    def refund(self, user, charge):
        with self.db() as db:
            db.execute("UPDATE payments SET refunded=1 WHERE charge=? AND user=?", (charge, user))

    def set_canceled(self, user):
        with self.db() as db:
            db.execute("INSERT INTO subscription_settings VALUES(?,1) ON CONFLICT(user) DO UPDATE SET canceled=1", (user,))

    def canceled(self, user):
        with self.db() as db:
            row = db.execute("SELECT canceled FROM subscription_settings WHERE user=?", (user,)).fetchone()
        return bool(row and row[0])

    def latest_charge(self, user):
        with self.db() as db:
            row = db.execute(
                "SELECT charge FROM payments WHERE user=? AND refunded=0 AND expires>? ORDER BY expires DESC LIMIT 1",
                (user, int(time.time()))).fetchone()
        return row[0] if row else None

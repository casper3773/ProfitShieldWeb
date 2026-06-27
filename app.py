from flask import Flask, render_template, jsonify, request, send_file, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import requests
import pandas as pd
import sqlite3
import io
import iyzipay
import json
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# 👇 app = Flask(__name__) SATIRININ HEMEN ALTINA EKLE 👇
# iYziCo API Ayarları (Sandbox/Test Ortamı)
IYZICO_API_KEY = os.environ.get('IYZICO_API_KEY', 'sandbox-test-api-key')
IYZICO_SECRET_KEY = os.environ.get('IYZICO_SECRET_KEY', 'sandbox-test-secret-key')
IYZICO_BASE_URL = 'https://sandbox-api.iyzipay.com' # Test ortamı URL'i

# iYziCo istemci seçeneklerini yapılandırıyoruz
iyzico_options = {
    'api_key': IYZICO_API_KEY,
    'secret_key': IYZICO_SECRET_KEY,
    'base_url': IYZICO_BASE_URL
}

app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'super-secret-key-change-this-later')
app.secret_key = app.config['SECRET_KEY']



login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

def init_db():
    conn = sqlite3.connect('profitshield.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            is_subscribed INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

init_db()

class User(UserMixin):
    def __init__(self, id, username, email, is_subscribed):
        self.id = id
        self.username = username
        self.email = email
        self.is_subscribed = bool(is_subscribed)

@login_manager.user_loader
def load_user(user_id):
    conn = sqlite3.connect('profitshield.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, email, is_subscribed FROM users WHERE id = ?", (user_id,))
    user_data = cursor.fetchone()
    conn.close()
    if user_data:
        return User(user_data[0], user_data[1], user_data[2], user_data[3])
    return None

def subscription_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({"success": False, "message": "Please login first."}), 401
        if not current_user.is_subscribed:
            return jsonify({"success": False, "message": "Subscription Required! Please upgrade your plan to process files."}), 403
        return f(*args, **kwargs)
    return decorated_function

def get_live_rate(currency_code="USD"):
    try:
        url = f"https://open.er-api.com/v6/latest/{currency_code}"
        response = requests.get(url, timeout=5)
        data = response.json()
        if data.get("result") == "success":
            return data['rates']['TRY']
        return None
    except:
        return None

# --- ROTALAR (ROUTES) ---

@app.route('/')
@login_required
def home():
    # 🚨 ESKİ LİNK YERİNE YENİ IYZICO ROTASINA YÖNLENDİRİYORUZ:
    if not current_user.is_subscribed:
        return redirect(url_for('iyzico_payment'))
        
    rate = get_live_rate("USD")
    rate_text = f"Live USD Rate: ₺{rate:.2f}" if rate else "Live Rate: Connection Error"
    return render_template('index.html', usd_rate=rate_text)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        
        # 🚨 GÜVENLİK DÜZELTMESİ: Yeni kayıt olan herkes kesinlikle 0 (Ücretsiz) başlar!
        is_subscribed = 0 
        
        hashed_password = generate_password_hash(password)
        
        try:
            conn = sqlite3.connect('profitshield.db')
            cursor = conn.cursor()
            cursor.execute("INSERT INTO users (username, email, password, is_subscribed) VALUES (?, ?, ?, ?)",
                           (username, email, hashed_password, is_subscribed))
            conn.commit()
            conn.close()
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            return "Username or Email already exists!"
            
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        conn = sqlite3.connect('profitshield.db')
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, email, password, is_subscribed FROM users WHERE email = ?", (email,))
        user_data = cursor.fetchone()
        conn.close()
        
        if user_data and check_password_hash(user_data[3], password):
            user_obj = User(user_data[0], user_data[1], user_data[2], user_data[4])
            login_user(user_obj)
            return redirect(url_for('home'))
        else:
            return "Invalid email or password!"
            
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.route('/api/read-columns', methods=['POST'])
@login_required
def read_columns():
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "No file uploaded"})
    file = request.files['file']
    try:
        filename = file.filename.lower()
        file_bytes = file.read()
        if filename.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(file_bytes))
        else:
            df = pd.read_excel(io.BytesIO(file_bytes))
        columns = df.columns.tolist()
        preview_data = df.head(5).fillna('').to_dict(orient='records')
        return jsonify({"success": True, "columns": columns, "preview": preview_data})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@app.route('/api/get-rate/<currency>')
def get_rate_api(currency):
    symbol_map = {"$": "USD", "₺": "TRY", "€": "EUR", "£": "GBP"}
    code = symbol_map.get(currency, "USD")
    
    if code == "TRY":
        return jsonify({"success": True, "text": "Base Currency: ₺1.00"})
        
    try:
        url = f"https://open.er-api.com/v6/latest/{code}"
        res = requests.get(url, timeout=5).json()
        if res.get("result") == "success":
            rate = res['rates']['TRY']
            return jsonify({"success": True, "text": f"Live {code} Rate: ₺{rate:.2f}"})
    except:
        pass
    return jsonify({"success": False, "text": "Rate Connection Error"})

@app.route('/api/process-report', methods=['POST'])
@login_required
@subscription_required
def process_report():
    try:
        file = request.files.get('file')
        cost_column = request.form.get('cost_column')
        vat = float(request.form.get('vat', 0)) / 100
        comm = float(request.form.get('comm', 0)) / 100
        shipping = float(request.form.get('shipping', 0))
        target_profit = float(request.form.get('target_profit', 0)) / 100
        currency_symbol = request.form.get('currency', '$')
        
        filename = file.filename.lower()
        file_bytes = file.read()
        if filename.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(file_bytes))
        else:
            df = pd.read_excel(io.BytesIO(file_bytes))

        df[cost_column] = pd.to_numeric(df[cost_column], errors='coerce').fillna(0)
        payda = 1 - comm - vat - target_profit
        if payda <= 0: payda = 0.01  
            
        df['Target_Sale_Price'] = (df[cost_column] + shipping) / payda
        df['Estimated_VAT'] = df['Target_Sale_Price'] * vat
        df['Estimated_Commission'] = df['Target_Sale_Price'] * comm
        df['Net_Profit'] = df['Target_Sale_Price'] * target_profit

        total_revenue = float(df['Target_Sale_Price'].sum())
        total_profit = float(df['Net_Profit'].sum())
        avg_margin = float((total_profit / total_revenue * 100)) if total_revenue > 0 else 0

        product_col = None
        for col in df.columns:
            if col.lower() in ['product', 'product name', 'ürün', 'urun', 'name', 'item']:
                product_col = col
                break
        
        chart_labels = df[product_col].head(8).astype(str).tolist() if product_col else [f"Item {i+1}" for i in range(min(8, len(df)))]
        chart_values = df['Net_Profit'].head(8).round(2).tolist()

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='ProfitShield Report')
        output.seek(0)

        response = send_file(
            output, 
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 
            as_attachment=True, 
            download_name="ProfitShield_Target_Prices.xlsx"
        )
        
        response.headers['X-Total-Revenue'] = f"{currency_symbol}{total_revenue:,.2f}"
        response.headers['X-Total-Profit'] = f"{currency_symbol}{total_profit:,.2f}"
        response.headers['X-Average-Margin'] = f"{avg_margin:.1f}%"
        response.headers['X-Chart-Labels'] = json.dumps(chart_labels, ensure_ascii=False)
        response.headers['X-Chart-Values'] = json.dumps(chart_values)
        response.headers['Access-Control-Expose-Headers'] = 'X-Total-Revenue, X-Total-Profit, X-Average-Margin, X-Chart-Labels, X-Chart-Values'
        return response
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/iyzico-payment')
@login_required
def iyzico_payment():
    """Kullanıcıyı iYziCo test ödeme formuna hazırlar ve formu ekranda gösterir."""
    buyer = {
        'id': str(current_user.id),
        'name': current_user.username,
        'surname': 'User', # Boş kalmaması için geçici default değer
        'gsmNumber': '+905000000000',
        'email': current_user.email,
        'identityNumber': '11111111111', # Test ortamı için sahte TC
        'lastLoginDate': '2026-06-27 00:00:00',
        'registrationDate': '2026-06-27 00:00:00',
        'registrationAddress': 'Istanbul, Turkey',
        'ip': request.remote_addr,
        'city': 'Istanbul',
        'country': 'Turkey'
    }
    
    address = {
        'contactName': current_user.username,
        'city': 'Istanbul',
        'country': 'Turkey',
        'address': 'Istanbul, Turkey'
    }

    # Ödeme yapılacak ürün bilgisi
    basket_item = {
        'id': 'PRO_CLUB_01',
        'name': 'Profit Shield PRO Membership',
        'category': 'Software/SaaS',
        'itemType': 'VIRTUAL',
        'price': '299.00' # Aylık abonelik veya tek çekim test fiyatı (TL)
    }

    request_data = {
        'locale': 'tr',
        'conversationId': f"盾_{current_user.id}", # Benzersiz bir işlem ID'si
        'price': '299.00',
        'paidPrice': '299.00',
        'currency': 'TRY',
        'basketId': f"BASKET_{current_user.id}",
        'paymentGroup': 'PRODUCT',
        # Ödeme bitince iYziCo'nun kullanıcıyı geri göndereceği Flask rotamız (Tam URL olmalı):
        'callbackUrl': url_for('iyzico_callback', _external=True),
        'buyer': buyer,
        'shippingAddress': address,
        'billingAddress': address,
        'basketItems': [basket_item]
    }

    # iYziCo API'sine formu oluşturması için istek atıyoruz
    checkout_form_initialize = iyzipay.CheckoutFormInitialize().create(request_data, iyzico_options)
    
    # iYziCo'nun bize ürettiği HTML form kodunu alıyoruz
    payment_form_html = checkout_form_initialize.get('checkoutFormContent')
    
    # Formu ekranda render etmek için yeni bir HTML şablonuna göndereceğiz
    return render_template('iyzico_payment.html', payment_form=payment_form_html)


@app.route('/iyzico-callback', methods=['POST'])
def iyzico_callback():
    """iYziCo ödeme sonucunu buraya POST eder. Başarılıysa kullanıcıyı PRO yaparız."""
    token = request.form.get('token')
    
    request_data = {
        'locale': 'tr',
        'token': token
    }
    
    # iYziCo'ya "Bu token'lı ödeme gerçekten başarılı oldu mu?" diye soruyoruz
    checkout_form = iyzipay.CheckoutForm().retrieve(request_data, iyzico_options)
    payment_status = checkout_form.get('paymentStatus')
    
    if payment_status == 'SUCCESS':
        # Ödemeyi yapan kullanıcının ID'sini iYziCo'ya gönderdiğimiz conversationId içinden geri çekiyoruz
        conversation_id = checkout_form.get('conversationId')
        user_id = conversation_id.split('_')[1]
        
        # Veritabanında aboneyi PRO (1) yapıyoruz
        conn = sqlite3.connect('profitshield.db')
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_subscribed = 1 WHERE id = ?", (user_id,))
        conn.commit()
        conn.close()
        
        flash("Ödeme başarılı! Profit Shield PRO dünyasına hoş geldiniz.", "success")
        return redirect(url_for('home'))
    else:
        flash("Ödeme işlemi başarısız oldu veya iptal edildi.", "danger")
        return redirect(url_for('home'))


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, desc, or_
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from ai_analyzer import analyze_parcel_image
import os
import datetime

basedir = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key_12345'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'project.db')

app.config['UPLOAD_FOLDER'] = os.path.join(basedir, 'static', 'uploads')
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

db = SQLAlchemy(app)

# --- Models ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(30), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    is_admin = db.Column(db.Boolean, default=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Vehicle(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    vehicle_uid = db.Column(db.String(20), unique=True, nullable=False) 
    vehicle_type = db.Column(db.String(30), nullable=False) 
    plate_number = db.Column(db.String(20), unique=True, nullable=False) 
    driver_name = db.Column(db.String(100), nullable=True) 
    color = db.Column(db.String(30), nullable=True) 
    capacity_m3 = db.Column(db.Float, nullable=False, default=1.0) 
    status = db.Column(db.String(30), nullable=False, default='Available') 
    batches = db.relationship('Batch', backref='vehicle', lazy=True)

class Batch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    batch_name = db.Column(db.String(50), unique=True, nullable=False)
    batch_type = db.Column(db.String(20), nullable=False)
    current_volume = db.Column(db.Float, default=0.0)
    max_volume = db.Column(db.Float, default=0.82)
    max_capacity = db.Column(db.Float, default=90.0)
    status = db.Column(db.String(30), default='In Progress') # In Progress -> Ready -> Transporting -> Completed
    completion_time = db.Column(db.DateTime, nullable=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicle.id'), nullable=True)
    parcels = db.relationship('Parcel', backref='batch', lazy=True)

class Parcel(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    external_parcel_id = db.Column(db.String(100), nullable=True)
    dimensions = db.Column(db.String(50), nullable=True)
    weight = db.Column(db.Float, nullable=True)
    estimated_volume = db.Column(db.Float, default=0.0)
    parcel_name = db.Column(db.String(100))
    delivery_address = db.Column(db.String(200), nullable=False, default="Factory Client A")
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    batch_id = db.Column(db.Integer, db.ForeignKey('batch.id'), nullable=True)
    created_time = db.Column(db.DateTime, default=datetime.datetime.utcnow)

# --- Routes ---

@app.route("/")
def home(): return render_template("index.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            session['user_id'] = user.id
            session['username'] = user.username
            session['is_admin'] = user.is_admin
            return redirect(url_for('dashboard'))
        else:
            flash('Login failed.', 'error')
            return render_template("login.html")
    else:
        return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form['username']
        password = request.form['password']
        if User.query.filter_by(username=username).first():
            flash('Username exists.', 'error'); return render_template("register.html")
        is_admin = True if username.lower() == 'admin' else False
        new_user = User(username=username, is_admin=is_admin)
        new_user.set_password(password)
        db.session.add(new_user); db.session.commit()
        return redirect(url_for('login'))
    return render_template("register.html")

@app.route("/logout")
def logout(): session.clear(); return redirect(url_for('login'))

# --- Dashboard: The Logic Hub ---
@app.route("/dashboard")
def dashboard():
    if 'username' not in session: return redirect(url_for('login'))

    if session.get('is_admin'):
        return render_template("dashboard.html", username=session['username'], is_admin=True)
    else:
        # Driver Logic: Fetch ALL vehicles and ALL assigned batches
        # 1. Find all vehicles for this driver
        my_vehicles = Vehicle.query.filter(func.lower(Vehicle.driver_name) == func.lower(session['username'])).all()
        
        # 2. Find batches assigned to these vehicles that are active
        vehicle_ids = [v.id for v in my_vehicles]
        # Drivers only care about 'Ready' (Waiting to start) or 'Transporting' (On the way)
        my_missions = Batch.query.filter(
            Batch.vehicle_id.in_(vehicle_ids), 
            Batch.status.in_(['Ready', 'Transporting'])
        ).all()
            
        return render_template("dashboard_driver.html", username=session['username'], vehicles=my_vehicles, missions=my_missions)

# --- Upload (Admin Only) ---
@app.route("/upload", methods=["GET", "POST"])
def upload():
    if not session.get('is_admin'): return redirect(url_for('dashboard'))
    
    if request.method == "POST":
        file = request.files.get('parcel_image')
        if file and file.filename:
            filename = secure_filename(file.filename)
            save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(save_path)
            try: results = analyze_parcel_image(save_path)
            except: return redirect(request.url)
            return render_template("confirm_upload.html", username=session['username'], image_filename=filename, ai_data=results)

    return render_template("upload.html", username=session['username'], batches=Batch.query.all())

@app.route("/confirm_parcel", methods=["POST"])
def confirm_parcel():
    if not session.get('is_admin'): return redirect(url_for('login'))
    try:
        filename = request.form['image_filename']
        external_id = request.form['external_parcel_id']
        address_input = request.form.get('delivery_address')
        dimensions = request.form['dimensions']
        weight = float(request.form['weight'])
        real_volume = float(request.form['estimated_volume'])
        
        required_batch_type = 'small'
        if real_volume < 0.01: 
            required_batch_type = 'small'
        elif real_volume < 0.05: 
            required_batch_type = 'medium'
        else: 
            required_batch_type = 'large'
        
        target_batch = Batch.query.filter_by(status='In Progress', batch_type=required_batch_type).first()
        
        if not target_batch:
            ts = datetime.datetime.now().strftime("%H%M%S")
            auto_name = f"Auto-{ts} ({required_batch_type})"
            default_max_vol = 2.0 
            if required_batch_type == 'small': default_max_vol = 0.5
            elif required_batch_type == 'large': default_max_vol = 5.0
            
            target_batch = Batch(batch_name=auto_name, batch_type=required_batch_type, max_volume=default_max_vol, status='In Progress', vehicle_id=None)
            db.session.add(target_batch); db.session.commit()
            flash(f'New Batch Created: {auto_name}', 'info')
        
        new_parcel = Parcel(parcel_name=filename, external_parcel_id=external_id, delivery_address=address_input, dimensions=dimensions, weight=weight, estimated_volume=real_volume, user_id=session['user_id'], batch_id=target_batch.id)
        db.session.add(new_parcel)
        
        target_batch.current_volume += real_volume
        # Check Full
        if (target_batch.current_volume / target_batch.max_volume) * 100 >= target_batch.max_capacity:
            target_batch.status = 'Full'
            ts = datetime.datetime.now().strftime("%H%M%S")
            new_auto_name = f"Auto-{ts} ({required_batch_type})"
            new_batch = Batch(batch_name=new_auto_name, batch_type=required_batch_type, max_volume=target_batch.max_volume, status='In Progress', vehicle_id=None)
            db.session.add(new_batch)
            flash(f'Batch Full! Auto-created next batch: {new_auto_name}', 'warning')

        db.session.commit()
        flash(f'Saved to {target_batch.batch_name}.', 'success')
        return redirect(url_for('upload'))
    except Exception as e:
        db.session.rollback(); flash(f'Error: {e}', 'error'); return redirect(url_for('upload'))

# --- Assign Vehicle (Modified) ---
@app.route("/batch/<int:batch_id>/assign", methods=["POST"])
def assign_vehicle(batch_id):
    if not session.get('is_admin'): return redirect(url_for('login'))
    vid = request.form.get('vehicle_id')
    batch = Batch.query.get_or_404(batch_id)
    vehicle = Vehicle.query.get(vid)
    
    # Warning Logic
    if vehicle.capacity_m3 < batch.current_volume:
        flash(f'⚠️ WARNING: Load ({batch.current_volume}) exceeds Capacity ({vehicle.capacity_m3})!', 'error')
    else:
        flash(f'Vehicle {vehicle.vehicle_uid} assigned.', 'success')

    batch.vehicle_id = vehicle.id
    batch.max_volume = vehicle.capacity_m3
    # UPDATE: Don't set to Transporting yet. Set to 'Reserved'.
    vehicle.status = 'Reserved' 
    db.session.commit()
    return redirect(url_for('batch_detail', batch_id=batch.id))

# --- Admin Dispatch (New "Finalize") ---
@app.route("/batch/<int:batch_id>/dispatch", methods=["POST"])
def batch_dispatch(batch_id):
    if not session.get('is_admin'): return redirect(url_for('login'))
    batch = Batch.query.get_or_404(batch_id)
    if not batch.vehicle: return redirect(url_for('batch_detail', batch_id=batch.id))

    load_percent = (batch.current_volume / batch.max_volume) * 100
    
    batch.status = 'Ready'
    db.session.commit()
    
    if load_percent < 70:
        flash(f'Batch Dispatched (⚠️ Note: Load was only {int(load_percent)}%)', 'warning')
    else:
        flash('Batch Dispatched Successfully!', 'success')
        
    return redirect(url_for('batch_list'))

# --- Driver Actions (Interaction) ---

@app.route("/driver/start/<int:batch_id>", methods=["POST"])
def driver_start_mission(batch_id):
    if session.get('is_admin'): return redirect(url_for('dashboard')) # Only Driver
    batch = Batch.query.get_or_404(batch_id)
    
    # Logic: Start the engine
    batch.status = 'Transporting'
    if batch.vehicle:
        batch.vehicle.status = 'Transporting'
    
    db.session.commit()
    flash('Mission Started! Drive safely.', 'success')
    return redirect(url_for('dashboard'))

@app.route("/driver/complete/<int:batch_id>", methods=["POST"])
def driver_complete_mission(batch_id):
    if session.get('is_admin'): return redirect(url_for('dashboard'))
    batch = Batch.query.get_or_404(batch_id)
    
    # Logic: Arrived
    batch.status = 'Completed'
    batch.completion_time = datetime.datetime.now()
    if batch.vehicle:
        batch.vehicle.status = 'Available' # Free up the vehicle
    
    db.session.commit()
    flash('Mission Completed! Good job.', 'success')
    return redirect(url_for('dashboard'))

# --- Management Routes ---

@app.route("/parcel_list")
def parcel_list():
    if 'username' not in session: return redirect(url_for('login'))
    search = request.args.get('search_query')
    query = Parcel.query
    if search: query = query.filter(or_(Parcel.external_parcel_id.ilike(f"%{search}%"), Parcel.parcel_name.ilike(f"%{search}%")))
    return render_template("parcel_list.html", username=session['username'], parcels=query.order_by(Parcel.id.desc()).all(), search_query=search)

@app.route("/parcel/<int:parcel_id>")
def parcel_detail(parcel_id):
    if 'username' not in session: return redirect(url_for('login'))
    return render_template("parcel_detail.html", username=session['username'], parcel=Parcel.query.get_or_404(parcel_id))

@app.route("/parcel/bulk_delete", methods=["POST"])
def parcel_bulk_delete():
    if not session.get('is_admin'): return redirect(url_for('dashboard'))
    ids = request.form.getlist("parcel_ids")
    if ids:
        parcels = Parcel.query.filter(Parcel.id.in_(ids)).all()
        for p in parcels:
            if p.batch and p.batch.status != 'Completed': p.batch.current_volume -= p.estimated_volume
        db.session.query(Parcel).filter(Parcel.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()
        flash('Deleted.', 'success')
    return redirect(url_for('parcel_list'))

@app.route("/batch_list")
def batch_list():
    if 'username' not in session: return redirect(url_for('login'))
    search = request.args.get('search_query')
    query = Batch.query
    if search: query = query.filter(Batch.batch_name.ilike(f"%{search}%"))
    return render_template("batch_list.html", username=session['username'], batches=query.order_by(Batch.id.desc()).all(), search_query=search)

@app.route("/batch/create", methods=["GET", "POST"])
def create_batch():
    if not session.get('is_admin'): return redirect(url_for('dashboard'))
    if request.method == "POST":
        try:
            base, btype, vid = request.form['batch_name'], request.form['batch_type'], request.form.get('vehicle_id')
            full = f"{base} ({btype})"
            if Batch.query.filter_by(batch_name=full).first(): flash('Exists.', 'error'); return redirect(url_for('create_batch'))
            if vid:
                veh = Vehicle.query.get(vid)
                mv, v_id, veh.status = veh.capacity_m3, veh.id, 'Transporting'
            else:
                v_id, mv = None, (0.5 if btype=='small' else (2.0 if btype=='medium' else 5.0))
            db.session.add(Batch(batch_name=full, batch_type=btype, max_volume=mv, status='In Progress', vehicle_id=v_id))
            db.session.commit(); flash('Created.', 'success'); return redirect(url_for('batch_list'))
        except Exception as e: db.session.rollback(); flash(f'Error: {e}', 'error')
    return render_template("create_batch.html", username=session['username'], vehicles=Vehicle.query.filter_by(status='Available').all())

@app.route("/batch/<int:batch_id>")
def batch_detail(batch_id):
    if 'username' not in session: return redirect(url_for('login'))
    return render_template("batch_detail.html", username=session['username'], batch=Batch.query.get_or_404(batch_id), vehicles=Vehicle.query.filter_by(status='Available').all())

@app.route("/batch/<int:batch_id>/edit", methods=["GET", "POST"])
def batch_edit(batch_id):
    if not session.get('is_admin'): return redirect(url_for('dashboard'))
    batch = Batch.query.get_or_404(batch_id)
    if request.method == "POST":
        try:
            batch.batch_name, batch.max_volume = request.form['batch_name'], float(request.form['max_volume'])
            nid = request.form['vehicle_id']
            if nid == "none":
                if batch.vehicle: batch.vehicle.status = 'Available'
                batch.vehicle_id = None
            elif batch.vehicle_id != int(nid):
                if batch.vehicle: batch.vehicle.status = 'Available'
                nv = Vehicle.query.get(int(nid)); nv.status = 'Transporting'; batch.vehicle_id = nv.id
            db.session.commit(); flash('Updated.', 'success'); return redirect(url_for('batch_list'))
        except: db.session.rollback(); flash('Update failed.', 'error')
    return render_template("batch_edit.html", username=session['username'], batch=batch, vehicles=Vehicle.query.filter((Vehicle.status == 'Available') | (Vehicle.id == batch.vehicle_id)).all())

@app.route("/batch/<int:batch_id>/finalize", methods=["POST"])
def batch_finalize_single(batch_id):
    b = Batch.query.get_or_404(batch_id)
    b.status, b.completion_time = 'Completed', datetime.datetime.now()
    if b.vehicle: b.vehicle.status = 'Available'
    db.session.commit()
    return redirect(url_for('batch_completion_show', batch_id=b.id))

@app.route("/batch/bulk_finalize", methods=["POST"])
def batch_bulk_finalize():
    ids = request.form.getlist("batch_ids")
    if not ids: return redirect(url_for('batch_list'))
    for b in Batch.query.filter(Batch.id.in_(ids), Batch.status=='Full').all():
        b.status, b.completion_time = 'Completed', datetime.datetime.now()
        if b.vehicle: b.vehicle.status = 'Available'
    db.session.commit(); flash('Finalized.', 'success'); return redirect(url_for('batch_list'))

@app.route("/batch/bulk_delete", methods=["POST"])
def batch_bulk_delete():
    if not session.get('is_admin'): return redirect(url_for('batch_list'))
    ids = request.form.getlist("batch_ids")
    if ids:
        Parcel.query.filter(Parcel.batch_id.in_(ids)).delete(synchronize_session=False)
        Batch.query.filter(Batch.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit(); flash('Deleted.', 'success')
    return redirect(url_for('batch_list'))

@app.route("/batch/<int:batch_id>/parcels")
def batch_parcel_list(batch_id):
    if 'username' not in session: return redirect(url_for('login'))
    return render_template("batch_parcel_list.html", username=session['username'], batch=Batch.query.get_or_404(batch_id))

@app.route("/batch_completion/<int:batch_id>")
def batch_completion_show(batch_id):
    if 'username' not in session: return redirect(url_for('login'))
    return render_template("batch_completion.html", username=session['username'], batch=Batch.query.get_or_404(batch_id))

# --- Vehicle (Admin) ---
@app.route("/vehicle_list")
def vehicle_list():
    if not session.get('is_admin'): return redirect(url_for('dashboard'))
    search = request.args.get('search_query')
    query = Vehicle.query
    if search: query = query.filter(or_(Vehicle.plate_number.ilike(f"%{search}%"), Vehicle.driver_name.ilike(f"%{search}%")))
    return render_template("vehicle_list.html", username=session['username'], vehicles=query.order_by(Vehicle.id.asc()).all(), search_query=search)

@app.route("/vehicle/add", methods=["GET", "POST"])
def vehicle_add():
    if not session.get('is_admin'): return redirect(url_for('dashboard'))
    if request.method == "POST":
        uid, plate = request.form['vehicle_uid'], request.form['plate_number']
        if Vehicle.query.filter((Vehicle.vehicle_uid==uid)|(Vehicle.plate_number==plate)).first():
            flash('Duplicate.', 'error'); return render_template("add_vehicle.html", username=session['username'])
        db.session.add(Vehicle(vehicle_uid=uid, vehicle_type=request.form['vehicle_type'], plate_number=plate, driver_name=request.form.get('driver_name'), color=request.form.get('color'), capacity_m3=float(request.form['capacity_m3']), status=request.form['status']))
        db.session.commit(); flash('Added.', 'success'); return redirect(url_for('vehicle_list'))
    return render_template("add_vehicle.html", username=session['username'])

@app.route("/vehicle/<int:vehicle_id>/edit", methods=["GET", "POST"])
def vehicle_edit(vehicle_id):
    if not session.get('is_admin'): return redirect(url_for('dashboard'))
    v = Vehicle.query.get_or_404(vehicle_id)
    if request.method == "POST":
        v.vehicle_uid, v.vehicle_type, v.plate_number = request.form['vehicle_uid'], request.form['vehicle_type'], request.form['plate_number']
        v.driver_name, v.color, v.capacity_m3, v.status = request.form.get('driver_name'), request.form.get('color'), float(request.form['capacity_m3']), request.form['status']
        db.session.commit(); flash('Updated.', 'success'); return redirect(url_for('vehicle_list'))
    return render_template("vehicle_edit.html", username=session['username'], vehicle=v)

@app.route("/vehicle/<int:vehicle_id>/delete", methods=["POST"])
def vehicle_delete(vehicle_id):
    if not session.get('is_admin'): return redirect(url_for('dashboard'))
    v = Vehicle.query.get_or_404(vehicle_id)
    if Batch.query.filter_by(vehicle_id=vehicle_id, status='In Progress').first(): flash('Cannot delete active vehicle.', 'error'); return redirect(url_for('vehicle_list'))
    db.session.delete(v); db.session.commit(); flash('Deleted.', 'success'); return redirect(url_for('vehicle_list'))

# --- Settings ---
@app.route("/setting")
def setting():
    if 'username' not in session: return redirect(url_for('login'))
    return render_template("setting.html", username=session['username']) # Staff can access change password

@app.route("/setting/change_password", methods=["POST"])
def change_password():
    if 'username' not in session: return redirect(url_for('login'))
    u = User.query.filter_by(username=session['username']).first()
    if u.check_password(request.form['current_password']):
        u.set_password(request.form['new_password']); db.session.commit(); flash('Updated.', 'success')
    else: flash('Wrong password.', 'error')
    return redirect(url_for('setting'))

@app.route("/analysis")
def analysis():
    if 'username' not in session: return redirect(url_for('login'))
    today = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    top_v = db.session.query(Vehicle.vehicle_type, func.count(Batch.id).label('c')).join(Batch, Batch.vehicle_id==Vehicle.id).group_by(Vehicle.vehicle_type).order_by(desc('c')).first()
    stats = {
        "parcels_today": db.session.query(Parcel).filter(Parcel.created_time>=today).count(),
        "parcels_total": db.session.query(Parcel).count(),
        "small_count": db.session.query(Batch).filter_by(batch_type='small').count(),
        "medium_count": db.session.query(Batch).filter_by(batch_type='medium').count(),
        "large_count": db.session.query(Batch).filter_by(batch_type='large').count(),
        "recent_batches": Batch.query.order_by(Batch.id.desc()).limit(3).all(),
        "most_used_vehicle": top_v[0] if top_v else "N/A"
    }
    return render_template("analysis.html", username=session['username'], stats=stats)

with app.app_context():
    db.create_all()
    print("--- ✅ Database Tables Checked/Created ---")

if __name__ == "__main__":
    app.run(debug=True)
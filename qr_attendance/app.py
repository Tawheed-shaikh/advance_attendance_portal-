from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, Response
)
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
import qrcode
import csv
import io
import base64
import secrets
import os

# ---------------------------
# App config
# ---------------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = "change_this_to_a_secure_key"

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
instance_dir = os.path.join(BASE_DIR, "instance")
if not os.path.exists(instance_dir):
    os.makedirs(instance_dir)

db_path = os.path.join(instance_dir, "qr_attendance.db")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + db_path
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


# ---------------------------
# Models
# ---------------------------

class Admin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


class Teacher(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    roll_number = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(150), nullable=False)
    batch = db.Column(db.String(50), nullable=False)
    course = db.Column(db.String(50), nullable=False)
    year = db.Column(db.String(20), nullable=False)
    device_id = db.Column(db.String(200), nullable=True)


class ClassSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    course = db.Column(db.String(50), nullable=False)
    batch = db.Column(db.String(50), nullable=False)
    room = db.Column(db.String(50), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey("teacher.id"), nullable=False)
    date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)

    teacher = db.relationship("Teacher", backref="sessions")


class QRSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    class_session_id = db.Column(db.Integer, db.ForeignKey("class_session.id"), nullable=False)
    token = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    active = db.Column(db.Boolean, default=True)

    class_session = db.relationship("ClassSession", backref="qr_codes")


class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False)
    class_session_id = db.Column(db.Integer, db.ForeignKey("class_session.id"), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    status = db.Column(db.String(20), default="Present", nullable=False)

    student = db.relationship("Student", backref="attendance")
    class_session = db.relationship("ClassSession", backref="attendance")


# ---------------------------
# Helpers
# ---------------------------

def create_default_admin():
    """Create a default admin if none exists."""
    if Admin.query.first() is None:
        admin = Admin(username="admin")
        admin.set_password("admin123")
        db.session.add(admin)
        db.session.commit()
        print("Default admin created: username=admin password=admin123")


def generate_qr(data: str) -> str:
    """Return QR image as base64 string (PNG)."""
    qr = qrcode.QRCode(box_size=8, border=3)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def login_required(role: str) -> bool:
    """Simple role-based check."""
    return session.get("role") == role


# ---------------------------
# Auth Routes
# ---------------------------

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        login_type = request.form.get("login_type")
        username = request.form.get("username")
        password = request.form.get("password")

        user = None
        if login_type == "admin":
            user = Admin.query.filter_by(username=username).first()
        elif login_type == "teacher":
            user = Teacher.query.filter_by(username=username).first()

        if user and user.check_password(password):
            session["role"] = login_type
            session["user_id"] = user.id
            return redirect(f"/{login_type}/dashboard")

        flash("Invalid credentials", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------
# Admin Routes
# ---------------------------

@app.route("/admin/dashboard")
def admin_dashboard():
    if not login_required("admin"):
        return redirect(url_for("login"))
    totals = {
        "students": Student.query.count(),
        "teachers": Teacher.query.count(),
        "sessions": ClassSession.query.count(),
        "attendance": Attendance.query.count()
    }
    return render_template("admin_dashboard.html",
                           total_students=totals["students"],
                           total_teachers=totals["teachers"],
                           total_sessions=totals["sessions"],
                           total_attendance=totals["attendance"])


@app.route("/admin/add_student", methods=["GET", "POST"])
def admin_add_student():
    if not login_required("admin"):
        return redirect(url_for("login"))

    if request.method == "POST":
        roll = request.form.get("roll")
        name = request.form.get("name")
        batch = request.form.get("batch")
        course = request.form.get("course")
        year = request.form.get("year")
        device_id = request.form.get("device_id")

        if Student.query.filter_by(roll_number=roll).first():
            flash("This roll number already exists!", "danger")
            return redirect(request.url)

        s = Student(
            roll_number=roll,
            name=name,
            batch=batch,
            course=course,
            year=year,
            device_id=device_id
        )
        db.session.add(s)
        db.session.commit()
        flash("Student added successfully!", "success")
        return redirect(url_for("admin_dashboard"))

    return render_template("admin_add_student.html")


@app.route("/admin/students")
def admin_students():
    if not login_required("admin"):
        return redirect(url_for("login"))
    students = Student.query.order_by(Student.roll_number).all()
    return render_template("students_list.html", students=students)


@app.route("/admin/edit_student/<int:sid>", methods=["GET", "POST"])
def admin_edit_student(sid):
    if not login_required("admin"):
        return redirect(url_for("login"))

    student = Student.query.get_or_404(sid)

    if request.method == "POST":
        new_roll = request.form.get("roll")
        # if roll changed, ensure uniqueness
        if new_roll != student.roll_number:
            if Student.query.filter_by(roll_number=new_roll).first():
                flash("Another student with same roll number exists!", "danger")
                return redirect(request.url)
            student.roll_number = new_roll
        student.name = request.form.get("name")
        student.batch = request.form.get("batch")
        student.course = request.form.get("course")
        student.year = request.form.get("year")
        student.device_id = request.form.get("device_id") or None

        db.session.commit()
        flash("Student updated successfully!", "success")
        return redirect(url_for("admin_students"))

    return render_template("edit_student.html", student=student)


@app.route("/admin/delete_student/<int:sid>")
def admin_delete_student(sid):
    if not login_required("admin"):
        return redirect(url_for("login"))

    student = Student.query.get_or_404(sid)
    # Optionally: delete related attendance records first
    Attendance.query.filter_by(student_id=student.id).delete()
    db.session.delete(student)
    db.session.commit()
    flash("Student deleted successfully!", "success")
    return redirect(url_for("admin_students"))


@app.route("/admin/add_teacher", methods=["GET", "POST"])
def admin_add_teacher():
    if not login_required("admin"):
        return redirect(url_for("login"))

    if request.method == "POST":
        name = request.form.get("name")
        username = request.form.get("username")
        password = request.form.get("password")

        if Teacher.query.filter_by(username=username).first():
            flash("Username already exists!", "danger")
            return redirect(request.url)

        t = Teacher(name=name, username=username)
        t.set_password(password)
        db.session.add(t)
        db.session.commit()
        flash("Teacher added successfully!", "success")
        return redirect(url_for("admin_dashboard"))

    return render_template("admin_add_teacher.html")


@app.route("/admin/create_session", methods=["GET", "POST"])
def admin_create_session():
    if not login_required("admin"):
        return redirect(url_for("login"))

    teachers = Teacher.query.order_by(Teacher.name).all()
    if request.method == "POST":
        cs = ClassSession(
            course=request.form.get("course"),
            batch=request.form.get("batch"),
            room=request.form.get("room"),
            teacher_id=int(request.form.get("teacher")),
            date=datetime.strptime(request.form.get("date"), "%Y-%m-%d").date(),
            start_time=datetime.strptime(request.form.get("start"), "%H:%M").time(),
            end_time=datetime.strptime(request.form.get("end"), "%H:%M").time(),
        )
        db.session.add(cs)
        db.session.commit()
        flash("Session created!", "success")
        return redirect(url_for("admin_dashboard"))

    return render_template("admin_create_session.html", teachers=teachers)


@app.route("/admin/view_sessions")
def admin_view_sessions():
    if not login_required("admin"):
        return redirect(url_for("login"))
    sessions = ClassSession.query.order_by(ClassSession.date.desc()).all()
    return render_template("view_sessions.html", sessions=sessions)


@app.route("/admin/generate_qr/<int:cid>")
def admin_generate_qr(cid):
    if not login_required("admin"):
        return redirect(url_for("login"))

    session_obj = ClassSession.query.get_or_404(cid)

    # deactivate previous active QR sessions for this class
    QRSession.query.filter_by(class_session_id=cid, active=True).update({"active": False})

    token = secrets.token_urlsafe(16)
    now = datetime.utcnow()
    expiry = now + timedelta(seconds=30)

    qs = QRSession(
        class_session_id=cid,
        token=token,
        created_at=now,
        expires_at=expiry,
        active=True
    )
    db.session.add(qs)
    db.session.commit()

    # create a scan URL encoded in QR (students will open this)
    url = f"{request.host_url.rstrip('/')}/scan?sid={qs.id}&token={token}"
    qr_img = generate_qr(url)
    return render_template("admin_qr.html", qr=qr_img, expires=expiry, class_session=session_obj)


# ---------------------------
# Export Attendance (CSV)
# ---------------------------
@app.route("/admin/export", methods=["GET", "POST"])
def admin_export():
    if not login_required("admin"):
        return redirect(url_for("login"))

    sessions = ClassSession.query.order_by(ClassSession.date.desc()).all()

    if request.method == "POST":
        mode = request.form.get("mode")

        # Export by session
        if mode == "session":
            sid = int(request.form.get("session_id"))
            cs = ClassSession.query.get_or_404(sid)

            records = Attendance.query.filter_by(class_session_id=cs.id).all()

            si = io.StringIO()
            cw = csv.writer(si)
            cw.writerow([
                "Roll Number", "Student Name", "Batch", "Course",
                "Timestamp", "Status", "Session Course", "Session Batch",
                "Room", "Date"
            ])
            for r in records:
                s = r.student
                cw.writerow([
                    s.roll_number,
                    s.name,
                    s.batch,
                    s.course,
                    r.timestamp.isoformat(sep=' '),
                    r.status,
                    cs.course,
                    cs.batch,
                    cs.room,
                    cs.date.isoformat()
                ])

            output = si.getvalue()
            si.close()
            filename = f"attendance_session_{cs.id}_{cs.date.isoformat()}.csv"
            return Response(
                output,
                mimetype="text/csv",
                headers={"Content-Disposition": f"attachment; filename={filename}"}
            )

        # Export all records
        records = Attendance.query.order_by(Attendance.timestamp.desc()).all()
        si = io.StringIO()
        cw = csv.writer(si)
        cw.writerow([
            "SessionID", "Session Course", "Session Batch", "Room", "Session Date",
            "Roll Number", "Student Name", "Timestamp", "Status"
        ])
        for r in records:
            cs = r.class_session
            s = r.student
            cw.writerow([
                cs.id,
                cs.course,
                cs.batch,
                cs.room,
                cs.date.isoformat() if cs.date else "",
                s.roll_number,
                s.name,
                r.timestamp.isoformat(sep=' '),
                r.status
            ])

        output = si.getvalue()
        si.close()
        filename = f"attendance_all_{datetime.utcnow().date().isoformat()}.csv"
        return Response(
            output,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    return render_template("export_attendance.html", sessions=sessions)


# ---------------------------
# Student scan
# ---------------------------
@app.route("/scan", methods=["GET", "POST"])
def student_scan():
    sid = request.args.get("sid")
    token = request.args.get("token")

    if not sid or not token:
        return "Invalid QR parameters", 400

    qs = QRSession.query.get(sid)
    if not qs or qs.token != token or not qs.active:
        return "Invalid or inactive QR session", 400

    if datetime.utcnow() > qs.expires_at:
        return "QR expired", 400

    class_sess = qs.class_session

    if request.method == "POST":
        roll = request.form.get("roll")
        if not roll:
            flash("Please enter roll number", "danger")
            return redirect(request.url)

        student = Student.query.filter_by(
            roll_number=roll,
            batch=class_sess.batch,
            course=class_sess.course
        ).first()

        if not student:
            flash("Invalid roll number for this session", "danger")
            return redirect(request.url)

        # check duplicate
        if Attendance.query.filter_by(student_id=student.id, class_session_id=class_sess.id).first():
            return "Attendance already marked for this session", 200

        a = Attendance(student_id=student.id, class_session_id=class_sess.id)
        db.session.add(a)
        db.session.commit()
        return render_template("student_success.html", student=student, class_session=class_sess, qr_session=qs)

    return render_template("student_scan.html", class_session=class_sess, qr_session=qs)


# ---------------------------
# Teacher panel
# ---------------------------
@app.route("/teacher/dashboard")
def teacher_dashboard():
    if not login_required("teacher"):
        return redirect(url_for("login"))
    teacher_id = session.get("user_id")
    today = datetime.utcnow().date()
    sessions = ClassSession.query.filter_by(teacher_id=teacher_id, date=today).all()
    return render_template("teacher_dashboard.html", sessions=sessions)


@app.route("/teacher/session/<int:cid>")
def teacher_view(cid):
    if not login_required("teacher"):
        return redirect(url_for("login"))
    records = Attendance.query.filter_by(class_session_id=cid).all()
    return render_template("teacher_attendance.html", records=records)


# ---------------------------
# Init & Run
# ---------------------------
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        create_default_admin()

    app.run(host="0.0.0.0", port=5000, debug=True)


# to run the project put this in terminal
# py app.py

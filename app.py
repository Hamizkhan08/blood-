# ============================================================================
# MISSING ROUTES - Add these to your existing app.py
# ============================================================================

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    """User profile management"""
    if current_user.user_type == 'donor':
        conn = get_db_connection()
        try:
            with conn.cursor(dictionary=True) as cursor:
                cursor.execute("SELECT * FROM donors WHERE user_id=%s", (current_user.id,))
                donor_profile = cursor.fetchone()
                
                form = DonorProfileForm()
                
                if request.method == 'GET' and donor_profile:
                    # Pre-fill form with existing data
                    form.blood_group.data = donor_profile['blood_group']
                    form.address.data = donor_profile['address']
                    form.is_available.data = donor_profile['is_available']
                    form.medical_notes.data = donor_profile['medical_notes']
                
                if form.validate_on_submit():
                    if donor_profile:
                        # Update existing profile
                        cursor.execute("""
                            UPDATE donors SET blood_group=%s, address=%s, is_available=%s, medical_notes=%s
                            WHERE user_id=%s
                        """, (form.blood_group.data, form.address.data, form.is_available.data, 
                              form.medical_notes.data, current_user.id))
                    else:
                        # Create new profile
                        cursor.execute("""
                            INSERT INTO donors (user_id, blood_group, address, is_available, medical_notes)
                            VALUES (%s, %s, %s, %s, %s)
                        """, (current_user.id, form.blood_group.data, form.address.data, 
                              form.is_available.data, form.medical_notes.data))
                    
                    conn.commit()
                    flash('Profile updated successfully!', 'success')
                    return redirect(url_for('dashboard'))
                
                return render_template('donor_profile.html', form=form, donor_profile=donor_profile)
        finally:
            conn.close()
    else:
        return render_template('user_profile.html')

@app.route('/request-blood', methods=['GET', 'POST'])
@login_required
def request_blood():
    """Create blood request"""
    form = BloodRequestForm()
    
    if form.validate_on_submit():
        conn = get_db_connection()
        try:
            with conn.cursor(dictionary=True) as cursor:
                # Create blood request
                cursor.execute("""
                    INSERT INTO blood_requests (requester_id, patient_name, blood_group, quantity_required,
                                              urgency_level, hospital_name, hospital_address, contact_number, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (current_user.id, form.patient_name.data, form.blood_group.data, 
                      form.quantity_required.data, form.urgency_level.data, form.hospital_name.data,
                      form.hospital_address.data, form.contact_number.data, form.notes.data))
                
                request_id = cursor.lastrowid
                conn.commit()
                
                # Notify compatible donors
                compatible_groups = get_compatible_blood_groups(form.blood_group.data)
                placeholders = ','.join(['%s'] * len(compatible_groups))
                
                cursor.execute(f"""
                    SELECT u.id as user_id FROM donors d 
                    JOIN users u ON d.user_id = u.id 
                    WHERE d.blood_group IN ({placeholders}) AND d.is_available = TRUE
                """, compatible_groups)
                
                compatible_donors = cursor.fetchall()
                
                # Create notifications
                for donor in compatible_donors:
                    create_notification(
                        user_id=donor['user_id'],
                        title=f'Urgent Blood Request - {form.blood_group.data}',
                        message=f'Patient {form.patient_name.data} needs {form.quantity_required.data} units of {form.blood_group.data} blood at {form.hospital_name.data}. Urgency: {form.urgency_level.data}',
                        notification_type='warning' if form.urgency_level.data == 'Critical' else 'info'
                    )
                
                flash(f'Blood request created successfully! Notified {len(compatible_donors)} compatible donors.', 'success')
                return redirect(url_for('dashboard'))
        finally:
            conn.close()
    
    return render_template('request_blood.html', form=form)

@app.route('/search-donors')
@login_required
def search_donors():
    """Search for available donors"""
    blood_group = request.args.get('blood_group', '')
    
    conn = get_db_connection()
    try:
        with conn.cursor(dictionary=True) as cursor:
            if blood_group:
                compatible_groups = get_compatible_blood_groups(blood_group)
                placeholders = ','.join(['%s'] * len(compatible_groups))
                
                cursor.execute(f"""
                    SELECT d.*, u.first_name, u.last_name, u.phone_number 
                    FROM donors d
                    JOIN users u ON d.user_id = u.id
                    WHERE d.blood_group IN ({placeholders}) AND d.is_available = TRUE AND u.is_verified = TRUE
                """, compatible_groups)
            else:
                cursor.execute("""
                    SELECT d.*, u.first_name, u.last_name, u.phone_number 
                    FROM donors d
                    JOIN users u ON d.user_id = u.id
                    WHERE d.is_available = TRUE AND u.is_verified = TRUE
                """)
            
            donors_data = cursor.fetchall()
            
            # Convert to format expected by template (list of tuples)
            donors = []
            for row in donors_data:
                donor_dict = {k: v for k, v in row.items() if k.startswith(('blood_', 'address', 'is_', 'last_', 'medical_'))}
                user_dict = {k: v for k, v in row.items() if k in ['first_name', 'last_name', 'phone_number']}
                donors.append((donor_dict, user_dict))
            
            blood_groups = ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-']
            
            return render_template('search_donors.html', donors=donors, 
                                 blood_groups=blood_groups, selected_blood_group=blood_group)
    finally:
        conn.close()

@app.route('/respond-to-request/<int:request_id>')
@login_required
def respond_to_request(request_id):
    """Donor responds to blood request"""
    if current_user.user_type != 'donor':
        flash('Only donors can respond to requests.', 'danger')
        return redirect(url_for('dashboard'))
    
    conn = get_db_connection()
    try:
        with conn.cursor(dictionary=True) as cursor:
            # Get blood request
            cursor.execute("SELECT * FROM blood_requests WHERE id=%s", (request_id,))
            blood_request = cursor.fetchone()
            
            if not blood_request:
                flash('Blood request not found.', 'danger')
                return redirect(url_for('dashboard'))
            
            # Get donor profile
            cursor.execute("SELECT * FROM donors WHERE user_id=%s", (current_user.id,))
            donor_profile = cursor.fetchone()
            
            if not donor_profile:
                flash('Please complete your donor profile first.', 'warning')
                return redirect(url_for('profile'))
            
            # Check if donor already responded
            cursor.execute("SELECT * FROM donations WHERE request_id=%s AND donor_id=%s", 
                          (request_id, donor_profile['id']))
            existing_donation = cursor.fetchone()
            
            if existing_donation:
                flash('You have already responded to this request.', 'info')
                return redirect(url_for('dashboard'))
            
            # Create donation record
            cursor.execute("""
                INSERT INTO donations (request_id, donor_id, status)
                VALUES (%s, %s, %s)
            """, (request_id, donor_profile['id'], 'Pending'))
            
            conn.commit()
            
            # Create notification for requester
            create_notification(
                user_id=blood_request['requester_id'],
                title='Donor Response Received',
                message=f'A donor has responded to your blood request for {blood_request["patient_name"]}. Contact: {current_user.phone_number if hasattr(current_user, "phone_number") else "N/A"}',
                notification_type='success'
            )
            
            flash('Response sent successfully! The requester will be notified.', 'success')
            return redirect(url_for('dashboard'))
    finally:
        conn.close()

# Handle availability toggle in donor dashboard
@app.route('/dashboard', methods=['GET', 'POST'])
@login_required
def dashboard():
    if request.method == 'POST' and 'toggle_availability' in request.form:
        if current_user.user_type == 'donor':
            conn = get_db_connection()
            try:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        UPDATE donors SET is_available = NOT is_available WHERE user_id = %s
                    """, (current_user.id,))
                    conn.commit()
                    flash('Availability status updated!', 'success')
            finally:
                conn.close()
            return redirect(url_for('dashboard'))
    
    # Rest of dashboard logic (your existing code)
    conn = get_db_connection()
    try:
        with conn.cursor(dictionary=True) as cursor:
            if current_user.user_type == 'donor':
                cursor.execute("SELECT * FROM donors WHERE user_id=%s", (current_user.id,))
                donor_profile = cursor.fetchone()
                cursor.execute("SELECT * FROM blood_requests WHERE status='Active' ORDER BY created_at DESC LIMIT 5")
                recent_requests = cursor.fetchall()
                cursor.execute("""
                    SELECT d.* FROM donations d
                    JOIN donors dn ON d.donor_id = dn.id
                    WHERE dn.user_id=%s ORDER BY d.created_at DESC LIMIT 5
                """, (current_user.id,))
                my_donations = cursor.fetchall()
                return render_template('donor_dashboard.html', donor_profile=donor_profile,
                                     recent_requests=recent_requests, my_donations=my_donations)

            elif current_user.user_type == 'requester':
                cursor.execute("SELECT * FROM blood_requests WHERE requester_id=%s ORDER BY created_at DESC", (current_user.id,))
                my_requests = cursor.fetchall()
                return render_template('requester_dashboard.html', my_requests=my_requests)

            else:  # Admin
                cursor.execute("SELECT COUNT(*) AS total_users FROM users")
                total_users = cursor.fetchone()['total_users']
                cursor.execute("SELECT COUNT(*) AS total_donors FROM donors")
                total_donors = cursor.fetchone()['total_donors']
                cursor.execute("SELECT COUNT(*) AS active_requests FROM blood_requests WHERE status='Active'")
                active_requests = cursor.fetchone()['active_requests']
                cursor.execute("SELECT * FROM donations ORDER BY created_at DESC LIMIT 10")
                recent_donations = cursor.fetchall()
                return render_template('admin_dashboard.html', total_users=total_users,
                                     total_donors=total_donors, active_requests=active_requests,
                                     recent_donations=recent_donations)
    finally:
        conn.close()

# API Routes for notifications
@app.route('/api/notifications')
@login_required
def api_notifications():
    """Get user notifications"""
    conn = get_db_connection()
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute("""
                SELECT * FROM notifications 
                WHERE user_id=%s AND is_read=FALSE 
                ORDER BY created_at DESC
            """, (current_user.id,))
            notifications = cursor.fetchall()
            
            notification_list = []
            for notif in notifications:
                notification_list.append({
                    'id': notif['id'],
                    'title': notif['title'],
                    'message': notif['message'],
                    'type': notif['type'],
                    'created_at': notif['created_at'].strftime('%Y-%m-%d %H:%M')
                })
            
            return jsonify(notification_list)
    finally:
        conn.close()

@app.route('/api/mark-notification-read/<int:notification_id>')
@login_required
def api_mark_notification_read(notification_id):
    """Mark notification as read"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE notifications SET is_read=TRUE 
                WHERE id=%s AND user_id=%s
            """, (notification_id, current_user.id))
            conn.commit()
            
            if cursor.rowcount > 0:
                return jsonify({'status': 'success'})
            else:
                return jsonify({'status': 'error', 'message': 'Notification not found'})
    finally:
        conn.close()

# Admin routes
@app.route('/admin/verify-users')
@login_required
def admin_verify_users():
    """Admin page to verify users"""
    if current_user.user_type != 'admin':
        flash('Access denied. Admin only.', 'danger')
        return redirect(url_for('dashboard'))
    
    conn = get_db_connection()
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute("SELECT * FROM users WHERE is_verified=FALSE")
            unverified_users = cursor.fetchall()
            return render_template('admin_verify_users.html', users=unverified_users)
    finally:
        conn.close()

@app.route('/admin/verify-user/<int:user_id>')
@login_required
def admin_verify_user(user_id):
    """Verify a specific user"""
    if current_user.user_type != 'admin':
        flash('Access denied. Admin only.', 'danger')
        return redirect(url_for('dashboard'))
    
    conn = get_db_connection()
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute("SELECT * FROM users WHERE id=%s", (user_id,))
            user = cursor.fetchone()
            
            if not user:
                flash('User not found.', 'danger')
                return redirect(url_for('admin_verify_users'))
            
            cursor.execute("UPDATE users SET is_verified=TRUE WHERE id=%s", (user_id,))
            conn.commit()
            
            create_notification(
                user_id=user['id'],
                title='Account Verified',
                message='Your account has been verified by the admin. You can now receive blood requests.',
                notification_type='success'
            )
            
            flash(f'User {user["first_name"]} {user["last_name"]} has been verified.', 'success')
            return redirect(url_for('admin_verify_users'))
    finally:
        conn.close()

# Add missing template: user_profile.html route
@app.route('/user-profile')
@login_required
def user_profile():
    """Basic user profile for non-donors"""
    return render_template('user_profile.html')
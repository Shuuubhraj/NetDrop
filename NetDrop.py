from flask import Flask, request, redirect, url_for, send_from_directory, render_template_string, Response, jsonify, session
import os
import socket
import mimetypes
import time
from datetime import datetime
import uuid
from PIL import Image
from flask_socketio import SocketIO, emit
import secrets
import webbrowser
import qrcode
import io
import base64

app = Flask(__name__)
app.config['SECRET_KEY'] = secrets.token_hex(16)
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'Uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
socketio = SocketIO(app)

# Hardcoded OTP password
OTP_PASSWORD = "2812"

# Store file metadata
file_metadata = {}

def get_file_size(file_path):
    size = os.path.getsize(file_path)
    if size < 1024:
        return size, "B", "green"
    elif size < 1024 ** 2:
        return size / 1024, "KB", "green"
    elif size < 1024 ** 3:
        return size / (1024 ** 2), "MB", "yellow"
    else:
        return size / (1024 ** 3), "GB", "red"

def get_upload_time(file_path):
    return datetime.fromtimestamp(os.path.getctime(file_path)).strftime('%Y-%m-%d %H:%M:%S')

def generate_thumbnail(file_path, filename):
    if mimetypes.guess_type(file_path)[0].startswith('image'):
        return None
    return None

@app.route('/qr_code')
def qr_code():
    ip = get_local_ip()
    url = f"http://{ip}:5000"
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode('utf-8')
    return jsonify({'qr_image': f"data:image/png;base64,{img_str}"})

@app.route('/', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST' and 'login' in request.form:
        password = request.form['password']
        if password == OTP_PASSWORD:
            session['authenticated'] = True
            return redirect(url_for('upload_file'))
        else:
            return jsonify({'error': 'Invalid password'}), 401

    if 'authenticated' not in session:
        return render_template_string(HTML_TEMPLATE, files=[], show_pretext=False, require_login=True, file_count=0)

    if request.method == 'POST' and 'file' in request.files:
        files = request.files.getlist('file')
        for f in files:
            if f.filename:
                filename = f.filename
                base, ext = os.path.splitext(filename)
                counter = 1
                while os.path.exists(os.path.join(UPLOAD_FOLDER, filename)):
                    filename = f"{base}_{counter}{ext}"
                    counter += 1
                file_path = os.path.join(UPLOAD_FOLDER, filename)
                f.save(file_path)
                file_metadata[filename] = {
                    'size': os.path.getsize(file_path),
                    'upload_time': get_upload_time(file_path),
                    'tags': []
                }
                socketio.emit('file_update', {'filename': filename})
        return redirect(url_for('upload_file'))

    files = os.listdir(UPLOAD_FOLDER)
    file_count = len(files)
    file_details = []
    for file in files:
        file_path = os.path.join(UPLOAD_FOLDER, file)
        size, unit, color = get_file_size(file_path)
        details = {
            'name': file,
            'size': f"{size:.2f}",
            'unit': unit,
            'size_color': color,
            'upload_time': file_metadata.get(file, {}).get('upload_time', get_upload_time(file_path)),
            'is_image': mimetypes.guess_type(file_path)[0].startswith('image'),
            'tags': file_metadata.get(file, {}).get('tags', [])
        }
        file_details.append(details)
    return render_template_string(HTML_TEMPLATE, files=file_details, show_pretext=len(file_details) == 0, require_login=False, file_count=file_count)

@app.route('/logout')
def logout():
    session.pop('authenticated', None)
    return redirect(url_for('upload_file'))

@app.route('/files/<path:filename>')
def download_file(filename):
    if 'authenticated' not in session:
        return redirect(url_for('upload_file'))

    file_path = os.path.join(UPLOAD_FOLDER, filename)
    if not os.path.exists(file_path):
        return "File not found", 404

    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type:
        ext = os.path.splitext(filename)[1].lower()
        mime_types = {
            '.pdf': 'application/pdf',
            '.mp4': 'video/mp4',
            '.mkv': 'video/x-matroska',
            '.mov': 'video/quicktime',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.mp3': 'audio/mpeg',
            '.wav': 'audio/wav'
        }
        mime_type = mime_types.get(ext, 'application/octet-stream')

    total_size = os.path.getsize(file_path)
    def generate():
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                yield chunk
        socketio.emit('download_progress', {
            'filename': filename,
            'progress': 100,
            'speed': total_size / (time.time() - start_time) / 1024 if 'start_time' in locals() else 0
        })

    start_time = time.time()
    response = Response(generate(), mimetype=mime_type)
    response.headers['Content-Disposition'] = f'attachment; filename={filename}'
    response.headers['Content-Length'] = str(total_size)
    return response

@app.route('/delete/<path:filename>', methods=['POST'])
def delete_file(filename):
    if 'authenticated' not in session:
        return redirect(url_for('upload_file'))

    file_path = os.path.join(UPLOAD_FOLDER, filename)
    if os.path.exists(file_path):
        os.remove(file_path)
        if filename in file_metadata:
            del file_metadata[filename]
        socketio.emit('file_update', {'filename': filename, 'deleted': True})
    return redirect(url_for('upload_file'))

@app.route('/delete_all', methods=['POST'])
def delete_all():
    if 'authenticated' not in session:
        return redirect(url_for('upload_file'))

    files = os.listdir(UPLOAD_FOLDER)
    for file in files:
        file_path = os.path.join(UPLOAD_FOLDER, file)
        if os.path.exists(file_path):
            os.remove(file_path)
            if file in file_metadata:
                del file_metadata[file]
            socketio.emit('file_update', {'filename': file, 'deleted': True})
    return redirect(url_for('upload_file'))

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>NetDrop</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.2/css/all.min.css">
  <style>
    :root {
      --bg-color: #121212;
      --text-color: #e0e0e0;
      --card-bg: #1e1e1e;
      --card-border: #2a2a2a;
      --hover-bg: #333;
      --primary-btn: #2563eb;
      --secondary-btn: #10b981;
      --danger-btn: #ef4444;
      --neutral-btn: #374151;
      --accent: #8b5cf6;
    }

    body {
      margin: 0;
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background-color: var(--bg-color);
      color: var(--text-color);
      padding: 20px;
      transition: all 0.3s ease;
      overflow: auto;
    }

    .container {
      max-width: 1000px;
      margin: 0 auto;
    }

    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 20px;
    }

    h1 {
      font-size: 1.8rem;
      font-weight: 600;
      color: var(--text-color);
      display: flex;
      align-items: center;
      margin: 0; /* Ensure no extra margin disrupts alignment */
    }

    h1::before {
      content: "\f0c2"; /* Font Awesome cloud icon */
      font-family: "Font Awesome 6 Free";
      font-weight: 900;
      margin-right: 10px;
      color: var(--accent);
      font-size: 1.8rem; /* Match h1 size for consistency */
    }

    .upload-section {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 20px;
      margin-bottom: 20px;
      text-align: center;
      transition: transform 0.2s ease;
    }

    .upload-label {
      display: inline-block;
      background-color: var(--primary-btn);
      color: #fff;
      padding: 12px 24px;
      border-radius: 8px;
      cursor: pointer;
      font-size: 1rem;
      font-weight: 500;
      transition: background 0.2s ease;
    }

    .upload-label:hover {
      background-color: #1d4ed8;
    }

    .file-list {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 20px;
      min-height: 340px;
    }

    .file-item {
      display: flex;
      align-items: center;
      background-color: var(--card-bg);
      padding: 15px;
      border-radius: 8px;
      margin-bottom: 10px;
      transition: transform 0.2s ease, background 0.2s ease;
      animation: fadeIn 0.5s ease;
    }

    .file-item:hover {
      background-color: var(--hover-bg);
      transform: translateY(-2px);
    }

    .file-details {
      flex-grow: 1;
      margin-right: 15px;
    }

    .file-name {
      word-break: break-word;
      color: var(--text-color);
      font-size: 1rem;
      font-weight: 500;
      margin-bottom: 5px;
    }

    .file-meta {
      font-size: 0.85rem;
      color: #888;
    }

    .file-thumbnail {
      max-width: 60px;
      max-height: 60px;
      object-fit: contain;
      margin-right: 15px;
      border-radius: 6px;
      border: 1px solid var(--card-border);
    }

    .actions {
      display: flex;
      gap: 10px;
    }

    .btn {
      padding: 8px 16px;
      border-radius: 6px;
      font-size: 0.9rem;
      border: none;
      cursor: pointer;
      transition: background 0.2s ease;
    }

    .btn-download {
      background-color: var(--secondary-btn);
      color: #fff;
    }

    .btn-download:hover {
      background-color: #059669;
    }

    .btn-delete {
      background-color: var(--danger-btn);
      color: #fff;
    }

    .btn-delete:hover {
      background-color: #dc2626;
    }

    .btn-logout {
      background-color: var(--neutral-btn);
      color: #fff;
    }

    .btn-logout:hover {
      background-color: #4b5563;
    }

    .btn-delete-all {
      background-color: var(--danger-btn);
      color: #fff;
      padding: 10px 20px;
      border-radius: 6px;
      border: none;
      cursor: pointer;
      transition: background 0.2s ease;
      display: {{ 'block' if file_count > 10 else 'none' }};
      margin-top: 10px;
    }

    .btn-delete-all:hover {
      background-color: #dc2626;
    }

    .btn-qr {
      background-color: var(--neutral-btn);
      color: #fff;
      padding: 6px 12px;
      border-radius: 6px;
      border: none;
      cursor: pointer;
      font-size: 0.85rem;
      transition: background 0.2s ease;
      margin-left: 10px;
    }

    .btn-qr:hover {
      background-color: #4b5563;
    }

    .progress-container {
      width: 100%;
      margin-top: 10px;
      display: none;
    }

    .pretext {
      font-size: 0.9rem;
      color: #888;
      text-align: center;
      margin-top: 20px;
      display: {{ 'block' if show_pretext else 'none' }};
    }

    .footer {
      text-align: center;
      font-size: 0.85rem;
      color: #888;
      margin-top: 30px;
      display: flex;
      justify-content: center;
      align-items: center;
      gap: 10px;
    }

    .footer a {
      color: var(--accent);
      text-decoration: none;
    }

    .footer a:hover {
      text-decoration: underline;
    }

    .modal {
      display: {{ 'flex' if require_login else 'none' }};
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.7);
      justify-content: center;
      align-items: center;
      z-index: 1000;
      backdrop-filter: blur(5px);
    }

    .modal-content {
      background: var(--card-bg);
      padding: 25px;
      border: 1px solid var(--card-border);
      border-radius: 12px;
      text-align: center;
      max-width: 90%;
      box-shadow: 0 0 20px rgba(0, 0, 0, 0.4);
    }

    .modal h2 {
      font-size: 1.5rem;
      font-weight: 600;
      color: var(--text-color);
      margin-bottom: 20px;
    }

    .modal .otp-input {
      display: flex;
      gap: 10px;
      justify-content: center;
      margin-bottom: 20px;
    }

    .modal .otp-input input {
      width: 40px;
      height: 40px;
      text-align: center;
      font-size: 1.2rem;
      border: 1px solid var(--card-border);
      border-radius: 6px;
      background: var(--bg-color);
      color: var(--text-color);
      outline: none;
      transition: border-color 0.2s ease;
      type: text;
      pattern="[0-9]*";
    }

    .modal .otp-input input:focus {
      border-color: var(--accent);
    }

    .modal .login-btn {
      background-color: var(--primary-btn);
      color: #fff;
      padding: 10px 20px;
      border-radius: 6px;
      border: none;
      cursor: pointer;
      font-size: 1rem;
      font-weight: 500;
      transition: background 0.2s ease;
    }

    .modal .login-btn:hover {
      background-color: #1d4ed8;
    }

    .modal .btn-neutral {
      background-color: var(--neutral-btn);
      color: #fff;
      padding: 10px 20px;
      border-radius: 6px;
      border: none;
      cursor: pointer;
      font-size: 1rem;
      font-weight: 500;
      transition: background 0.2s ease;
    }

    .modal .btn-neutral:hover {
      background-color: #4b5563;
    }

    .qr-modal img {
      max-width: 200px;
      width: 100%;
      height: auto;
      margin: 20px auto;
      display: block;
    }

    .loader {
      border: 4px solid #f3f3f3;
      border-top: 4px solid var(--accent);
      border-radius: 50%;
      width: 24px;
      height: 24px;
      animation: spin 1s linear infinite;
      margin: 0 auto;
    }

    @keyframes spin {
      0% { transform: rotate(0deg); }
      100% { transform: rotate(360deg); }
    }

    @keyframes fadeIn {
      from { opacity: 0; }
      to { opacity: 1; }
    }

    @media (max-width: 600px) {
      .file-item {
        flex-direction: column;
        align-items: flex-start;
        gap: 10px;
      }

      .actions {
        width: 100%;
        justify-content: flex-start;
        gap: 10px;
      }

      .btn {
        width: 48%;
      }

      .btn-logout {
        width: 100px;
      }

      .modal .otp-input input {
        width: 30px;
        height: 30px;
      }

      .footer {
        flex-direction: column;
        gap: 5px;
      }

      .btn-qr {
        display: none;
      }
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1><i class="fas fa-cloud" style="margin-right: 10px; color: var(--accent); font-size: 1.8rem;"></i>NetDrop</h1>
      {% if not require_login %}
        <button class="btn btn-logout" onclick="window.location.href='/logout'">Logout</button>
      {% endif %}
    </div>

    <div class="upload-section">
      <form method="post" enctype="multipart/form-data" id="uploadForm">
        <label for="fileInput" class="upload-label">
          <i class="fas fa-upload"></i> Upload Files
        </label>
        <input type="file" id="fileInput" name="file" multiple onchange="uploadFiles()" style="display:none;">
      </form>
      <div class="progress-container" id="progressContainer">
        <progress id="progressBar" value="0" max="100" style="width:100%;"></progress>
        <div id="progressMessage" style="font-size: 0.9rem; color: #888; margin-top: 5px;"></div>
      </div>
    </div>

    <div class="file-list">
      <div id="fileList">
        {% for file in files %}
        <div class="file-item" data-name="{{ file.name }}">
          <img src="{{ url_for('download_file', filename=file.name) if file.is_image else '' }}"
               class="file-thumbnail lazy"
               loading="lazy"
               alt="{{ file.name }} thumbnail"
               style="display: {{ 'block' if file.is_image else 'none' }};">
          <div class="file-details">
            <div class="file-name">{{ file.name }}</div>
            <div class="file-meta">
              Size: <span style="color: {{ file.size_color }}">{{ file.size }} {{ file.unit }}</span>,
              Uploaded: {{ file.upload_time }}
            </div>
          </div>
          <div class="actions">
            <button class="btn btn-download" onclick="downloadFile('{{ file.name }}')">
              <i class="fas fa-download"></i> Download
            </button>
            <button class="btn btn-delete" onclick="showModal('{{ file.name }}')">
              <i class="fas fa-trash"></i> Delete
            </button>
          </div>
        </div>
        {% endfor %}
      </div>
      <div class="pretext">Upload files to share instantly</div>
      <button class="btn-delete-all" onclick="deleteAllFiles()">Delete All</button>
    </div>

    <div class="footer">
      <span>Network File Sharing by <a href="https://github.com/Shuuubhraj" target="_blank">@Shuuubhraj</a></span>
      <button class="btn btn-qr" onclick="showQRModal()">View QR</button>
    </div>
  </div>

  <div class="modal" id="loginModal">
    <div class="modal-content">
      <h2>Authentication Required</h2>
      <form id="loginForm" method="POST">
        <div class="otp-input">
          <input type="text" maxlength="1" pattern="[0-9]*" required>
          <input type="text" maxlength="1" pattern="[0-9]*" required>
          <input type="text" maxlength="1" pattern="[0-9]*" required>
          <input type="text" maxlength="1" pattern="[0-9]*" required>
        </div>
        <button type="submit" class="login-btn">Login</button>
      </form>
    </div>
  </div>

  <div class="modal" id="confirmModal" style="display: none;">
    <div class="modal-content">
      <p style="margin-bottom: 20px; font-size: 1rem;">Are you sure you want to delete this file?</p>
      <button class="btn btn-delete" onclick="deleteFile()">Yes</button>
      <button class="btn btn-neutral" onclick="hideModal()">Cancel</button>
    </div>
  </div>

  <div class="modal" id="qrModal" style="display: none;">
    <div class="modal-content qr-modal">
      <h2>Scan to Access</h2>
      <img id="qrImage" src="" alt="QR Code">
      <button class="btn btn-neutral" onclick="hideQRModal()">Close</button>
    </div>
  </div>

  <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.5.1/socket.io.min.js"></script>
  <script>
    const socket = io();
    let currentFileToDelete = '';

    document.addEventListener('DOMContentLoaded', () => {
      const loginModal = document.getElementById('loginModal');
      const loginForm = document.getElementById('loginForm');
      const confirmModal = document.getElementById('confirmModal');
      const qrModal = document.getElementById('qrModal');
      const otpInputs = loginModal.querySelectorAll('.otp-input input');

      if (loginModal.style.display === 'flex') {
        confirmModal.style.display = 'none';
        qrModal.style.display = 'none';
        otpInputs[0].focus();
      } else {
        confirmModal.style.display = 'none';
        qrModal.style.display = 'none';
      }

      otpInputs.forEach((input, index) => {
        input.addEventListener('input', (e) => {
          if (e.target.value.length === 1 && index < otpInputs.length - 1) {
            otpInputs[index + 1].focus();
          } else if (e.target.value.length > 1) {
            e.target.value = e.target.value.slice(0, 1);
            if (index < otpInputs.length - 1) otpInputs[index + 1].focus();
          }
        });

        input.addEventListener('keydown', (e) => {
          if (e.key === 'Backspace' && !input.value && index > 0) {
            otpInputs[index - 1].focus();
          }
        });
      });

      loginForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const password = Array.from(otpInputs).map(input => input.value).join('');
        fetch('/', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
          },
          body: 'login=1&password=' + encodeURIComponent(password)
        })
        .then(response => {
          if (response.ok) {
            location.reload();
          } else {
            return response.json().then(data => {
              if (data.error) {
                alert('Invalid password');
                otpInputs.forEach(input => input.value = '');
                otpInputs[0].focus();
              }
            });
          }
        })
        .catch(error => console.error('Error:', error));
      });
    });

    function uploadFiles() {
      const fileInput = document.getElementById('fileInput');
      const files = fileInput.files;
      if (!files.length) return;

      const formData = new FormData();
      for (let file of files) {
        formData.append('file', file);
      }

      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/', true);

      xhr.upload.onprogress = function(e) {
        if (e.lengthComputable) {
          const percentComplete = (e.loaded / e.total) * 100;
          const progressBar = document.getElementById('progressBar');
          const progressMessage = document.getElementById('progressMessage');
          document.getElementById('progressContainer').style.display = 'block';
          progressBar.value = percentComplete;
          progressMessage.textContent = `Uploading: ${percentComplete.toFixed(0)}%`;
        }
      };

      xhr.onload = function() {
        if (xhr.status == 200) {
          document.getElementById('fileInput').value = '';
          document.getElementById('progressContainer').style.display = 'none';
          document.getElementById('progressMessage').textContent = 'Upload completed!';
          setTimeout(() => document.getElementById('progressContainer').style.display = 'none', 2000);
        } else {
          document.getElementById('progressMessage').textContent = 'Upload failed!';
          document.getElementById('progressContainer').style.display = 'block';
        }
      };

      xhr.send(formData);
    }

    function downloadFile(filename) {
      window.location.href = '/files/' + encodeURIComponent(filename) + '?download=true';
    }

    function showModal(filename) {
      currentFileToDelete = filename;
      const confirmModal = document.getElementById('confirmModal');
      confirmModal.style.display = 'flex';
    }

    function hideModal() {
      const confirmModal = document.getElementById('confirmModal');
      confirmModal.style.display = 'none';
    }

    function showQRModal() {
      const qrModal = document.getElementById('qrModal');
      const qrImage = document.getElementById('qrImage');
      qrImage.src = '';
      qrModal.style.display = 'flex';
      fetch('/qr_code')
        .then(response => response.json())
        .then(data => {
          qrImage.src = data.qr_image;
        })
        .catch(error => console.error('Error fetching QR code:', error));
    }

    function hideQRModal() {
      const qrModal = document.getElementById('qrModal');
      qrModal.style.display = 'none';
    }

    function deleteFile() {
      const form = document.createElement('form');
      form.method = 'POST';
      form.action = '/delete/' + encodeURIComponent(currentFileToDelete);
      document.body.appendChild(form);
      form.submit();
      hideModal();
    }

    function deleteAllFiles() {
      if (confirm('Are you sure you want to delete all files?')) {
        const form = document.createElement('form');
        form.method = 'POST';
        form.action = '/delete_all';
        document.body.appendChild(form);
        form.submit();
      }
    }

    socket.on('file_update', function(data) {
      if (data.deleted) {
        document.querySelector(`.file-item[data-name="${data.filename}"]`)?.remove();
      } else {
        window.location.reload();
      }
    });

    socket.on('download_progress', function(data) {
      console.log(`Downloading ${data.filename}: ${data.progress.toFixed(2)}% at ${data.speed.toFixed(2)} KB/s`);
    });
  </script>
</body>
</html>
"""

if __name__ == '__main__':
    ip = get_local_ip()
    url = f"http://{ip}:5000"
    print(f"\n🌐 NetDrop running at: {url}")
    webbrowser.open(url)
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)
from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file, flash, abort
from flask_sqlalchemy import SQLAlchemy
from pymilvus import connections, Collection, FieldSchema, CollectionSchema, DataType, utility
import os
import json
import time
import torch
from PIL import Image, UnidentifiedImageError
from transformers import CLIPProcessor, CLIPModel
import torch.nn.functional as F
import threading
import subprocess
import platform
from flask_cors import CORS
import numpy as np
from deep_translator import GoogleTranslator
import hashlib
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
import logging
from psd_tools import PSDImage
import webbrowser
from threading import Timer
from collections import defaultdict


app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Для використання flash повідомлень

# Конфігурація бази даних
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'image_data.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Модель для збереження метаданих зображень у базі даних
class ImageMetadata(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    file_path = db.Column(db.String(500), nullable=False)
    file_size = db.Column(db.Integer)
    file_type = db.Column(db.String(50))
    created_at = db.Column(db.String(100))
    last_modified = db.Column(db.DateTime)
    embedding = db.Column(db.PickleType)  # Збереження ембедінгів як об'єкти
    file_hash = db.Column(db.String(64))  # Додаємо поле для збереження хешу файлу
    last_scanned = db.Column(db.DateTime)  # Час останнього сканування
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
   

# Глобальний словник для відстеження прогресу сканування
scan_progress = {}
scan_progress_lock = threading.Lock()

# Глобальний словник для відстеження статусу сканування кожної директорії
scan_status = {}  # Ключ: директорія, значення: словник зі станом
scan_status_lock = threading.Lock()
# Глобальний словник для збереження стану сканування
scan_state = {}
scan_state_lock = threading.Lock()

# Глобальні змінні для контролю потоків сканування
scan_stop_events = {}
scan_stop_events_lock = threading.Lock()

scan_threads = {}
scan_threads_lock = threading.Lock()


CONFIG_FILE = 'config.json'
DIRECTORIES_FILE = 'directories.json'
LOG_FILE = './logs.txt'
SCAN_STATE_FILE = 'scan_state.json'

CROPPED_FOLDER = './cropped'
if not os.path.exists(CROPPED_FOLDER):
    os.makedirs(CROPPED_FOLDER)


# Глобальні змінні для контролю сканування
manual_scan_active = False
manual_scan_stop_event = threading.Event()

SUPPORTED_FORMATS = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.psd')
MAX_FILE_SIZE_MB = 200

scheduler = BackgroundScheduler()


# Функція для ініціалізації файлу конфігурації, якщо його не існує
def initialize_config():
    if not os.path.exists(CONFIG_FILE):
        # Створюємо файл конфігурації зі стандартними значеннями
        default_config = {
            "similarity_threshold": 0.2,
            "max_results": 12,
            "recursive_scan": True,
            "scan_times": []  # За замовчуванням сканування о 3:00 ранку
        }
        with open(CONFIG_FILE, 'w') as f:
            json.dump(default_config, f, indent=4)
        print(f"Config file {CONFIG_FILE} created with default values.")

# Функція для зчитування конфігурації
def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            config_data = json.load(f)
            # Перевіряємо наявність необхідних ключів і встановлюємо значення за замовчуванням, якщо вони відсутні
            config_defaults = {
                "similarity_threshold": 0.2,
                "max_results": 12,
                "recursive_scan": True,
                "scan_times": ["03:00"]  # Значення за замовчуванням для часу сканування
            }
            for key, default_value in config_defaults.items():
                if key not in config_data:
                    config_data[key] = default_value
            return config_data
    else:
        # Якщо файл не існує, викликаємо функцію для його створення
        initialize_config()
        return load_config()  # Після створення завантажуємо новий файл

# Функція для збереження конфігурації
def save_config(config_data):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config_data, f, indent=4)
        

# Ініціалізуємо конфігурацію при запуску програми
initialize_config()
config = load_config()
similarity_threshold = config.get("similarity_threshold", 0.2)
max_results = config.get("max_results", 12)
recursive_scan = config.get("recursive_scan", True)
scan_times = config.get("scan_times", ["03:00"])


# Функція для отримання списку директорій
def get_directories():
    if os.path.exists(DIRECTORIES_FILE):
        with open(DIRECTORIES_FILE, 'r') as f:
            data = json.load(f)
            directories = data.get('directories', [])
            # Нормалізуємо кожну директорію
            directories = [os.path.normpath(os.path.abspath(d)).replace('\\', '/') for d in directories]
            return directories
    return []

def calculate_file_hash(file_path):
    """Обчислення хешу файлу з використанням SHA-256."""
    hash_sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_sha256.update(chunk)
    return hash_sha256.hexdigest()

# Головна сторінка з пошуком і налаштуваннями
@app.route('/')
def index():
    # Завантажуємо список директорій з файлу
    directories = get_directories()

    # Завантажуємо актуальну конфігурацію
    config = load_config()
    similarity_threshold = config.get("similarity_threshold", 0.2)
    max_results = config.get("max_results", 12)
    recursive_scan = config.get("recursive_scan", True)
    scan_times = config.get("scan_times", ["03:00"])

    # Завантажуємо логи з файлу
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r', encoding='utf-8', errors='replace') as f:
            logs = f.read()
    else:
        logs = "No logs yet."
    
    # Завантажуємо збережені стани сканування
    saved_scan_states = load_scan_state()
    has_saved_scan_state = bool(saved_scan_states)

    # Ініціалізація змінних для шаблону
    results = []
    search_query = None

    return render_template('index.html', 
        results=results, 
        directories=directories,
        recursive_scan=recursive_scan, 
        scan_times=scan_times, 
        logs=logs, 
        similarity_threshold=similarity_threshold, 
        max_results=max_results,
        saved_scan_states=saved_scan_states,
        search_query=search_query
    )

# Створюємо глобальний об'єкт Lock
log_lock = threading.Lock()

# logging.basicConfig(
    # filename=LOG_FILE,
    # level=logging.ERROR,
    # format='%(asctime)s - %(levelname)s - %(message)s',
    # datefmt='%Y-%m-%d %H:%M:%S'
# )

# def log_event(message):
    # logging.info(message)

def log_event(message):
    try:
        with log_lock:
            with open(LOG_FILE, 'a', encoding='utf-8') as log_file:
                log_file.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {message}\n")
    except Exception as e:
        print(f"Failed to write to log file: {e}")

# Ініціалізація пристрою, моделі та процесора
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
#device = torch.device("cpu")
print(f"Current device: {device}")
log_event(f"Current device: {device}")
clip_model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32").to(device)
clip_processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")

# Встановлюємо модель у режим оцінки
clip_model.eval()

def connect_to_milvus(max_retries=5, delay=5):
    for attempt in range(1, max_retries + 1):
        try:
            log_event(f"Attempting to connect to Milvus (Attempt {attempt}/{max_retries})")
            connections.connect(
                alias="default",
                host="localhost",
                port="19530"
            )
            if connections.has_connection("default"):
                log_event(f"Successfully connected to Milvus on attempt {attempt}.")
                print(f"Successfully connected to Milvus on attempt {attempt}.")
                return True
            else:
                log_event("Connection established but verification failed.")
                print("Connection established but verification failed.")
                raise Exception("Connection verification failed.")
        except Exception as e:
            log_event(f"Attempt {attempt}: Failed to connect to Milvus - {e}")
            if attempt < max_retries:
                log_event(f"Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                log_event("Max retries reached. Could not connect to Milvus.")
                return False
    return False

# Маршрути для логів
@app.route('/get_logs', methods=['GET'])
def get_logs_route():
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, 'rb') as f:  # Відкриваємо файл у бінарному режимі
                f.seek(0, os.SEEK_END)  # Переходимо в кінець файлу
                file_size = f.tell()  # Отримуємо розмір файлу

                # Читаємо тільки останні 10 000 байтів або менше, якщо файл менший
                bytes_to_read = min(10000, file_size)
                f.seek(-bytes_to_read, os.SEEK_END)

                # Декодуємо з кодуванням UTF-8, некоректні символи замінюємо
                logs = f.read().decode('utf-8', errors='replace')
        except Exception as e:
            logs = f"Error reading logs: {str(e)}"
    else:
        logs = "No logs yet."
    return logs

@app.route('/clear_logs', methods=['POST'])
def clear_logs_route():
    # Очищаємо файл логів
    with open(LOG_FILE, 'w', encoding='utf-8') as log_file:  # Вказуємо 'utf-8'
        log_file.write('')  # Просто записуємо пустий рядок у файл
    return '', 204  # Повертаємо успішний статус без вмісту

# Маршрут для сервісу зображень
@app.route('/image/<int:image_id>')
def serve_image(image_id):
    # Отримуємо метадані зображення з бази даних
    image_metadata = db.session.get(ImageMetadata, image_id)
    if not image_metadata:
        abort(404)
    original_path = image_metadata.file_path
    if not os.path.exists(original_path):
        abort(404)
    
    # Якщо це PSD-файл, конвертуємо його у повнорозмірне зображення
    if original_path.lower().endswith('.psd'):
        try:
            psd = PSDImage.open(original_path)
            image = psd.composite()
            # Зберігаємо тимчасовий файл
            temp_image_path = os.path.join('static', 'temp', f"{image_id}_full.png").replace('\\', '/')
            if not os.path.exists('static/temp'):
                os.makedirs('static/temp')
            image.save(temp_image_path, 'PNG')
            return send_file(temp_image_path, mimetype='image/png')
        except Exception as e:
            print(f"Error generating full image for {original_path}: {e}")
            abort(500)
    else:
        # Якщо це не PSD-файл, відправляємо його безпосередньо
        return send_file(original_path)


# Маршрут для відкриття папки
@app.route('/open_folder', methods=['GET'])
def open_folder_route():
    folder_path = request.args.get('path')

    if folder_path:
        try:
            # Для Windows
            if platform.system() == "Windows":
                # Використовуємо explorer з опцією select, щоб відкрити папку з виділеним файлом
                folder_path = folder_path.replace("/", "\\")  # Перетворюємо на Windows-формат шляху
                subprocess.Popen(f'explorer /select,"{folder_path}"')
            # Для macOS
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", "-R", folder_path])  # Відкриває папку з виділеним файлом
            # Для Linux
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(folder_path)])
            
            return jsonify({"success": True}), 200
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
    else:
        return jsonify({"success": False, "error": "No path provided"}), 400

# Маршрути для додавання та видалення директорій
@app.route('/add_directory', methods=['POST'])
def add_directory():
    new_directory = request.form.get('directory')

    if not new_directory:
        flash("No directory provided.")
        return redirect(url_for('index'))

    # Нормалізуємо шлях
    new_directory = os.path.normpath(os.path.abspath(new_directory)).replace('\\', '/')

    # Завантажуємо існуючі директорії
    if os.path.exists(DIRECTORIES_FILE):
        with open(DIRECTORIES_FILE, 'r') as f:
            data = json.load(f)
    else:
        data = {'directories': []}

    # Додаємо нову директорію
    if new_directory not in data['directories']:
        data['directories'].append(new_directory)
        log_event(f"Added directory: {new_directory}")
        
        # Ініціалізуємо прогрес сканування
        with scan_progress_lock:
            scan_progress[new_directory] = 0
        
    else:
        log_event(f"Directory already exists: {new_directory}")

    # Зберігаємо список директорій у файл
    with open(DIRECTORIES_FILE, 'w') as f:
        json.dump(data, f)

    return redirect(url_for('index'))

@app.route('/delete_directory', methods=['POST'])
def remove_directory():
    directory_to_delete = request.form.get('directory')

    if not directory_to_delete:
        flash("No directory provided to delete.")
        return redirect(url_for('index'))

    # Нормалізуємо шлях
    directory_to_delete = os.path.normpath(os.path.abspath(directory_to_delete)).replace('\\', '/')

    # Завантажуємо список директорій
    if os.path.exists(DIRECTORIES_FILE):
        with open(DIRECTORIES_FILE, 'r') as f:
            data = json.load(f)
    else:
        data = {'directories': []}

    # Видаляємо директорію
    if directory_to_delete in data['directories']:
        data['directories'].remove(directory_to_delete)

        # Зберігаємо зміни у файл
        with open(DIRECTORIES_FILE, 'w') as f:
            json.dump(data, f)

        log_event(f"Deleted directory: {directory_to_delete}")

        # Видаляємо прогрес сканування для цієї директорії
        with scan_progress_lock:
            if directory_to_delete in scan_progress:
                del scan_progress[directory_to_delete]

        # Видаляємо всі записи зображень у цій директорії з бази даних
        with app.app_context():
            images_in_directory = ImageMetadata.query.filter(
                ImageMetadata.file_path.like(f'{directory_to_delete}%')).all()
            
            # Лічильник кількості видалених файлів
            deleted_count = len(images_in_directory)

            for image in images_in_directory:
                db.session.delete(image)
                delete_embedding_from_milvus(image.id)
            
            db.session.commit()
        
        log_event(f"Deleted {deleted_count} images from database and Milvus for directory: {directory_to_delete}")
    else:
        log_event(f"Directory to delete not found: {directory_to_delete}")

    return redirect(url_for('index'))

# Глобальний словник для збереження стану сканування
scan_state = {}
scan_state_lock = threading.Lock()

def save_scan_state(directory, state):
    directory = os.path.normpath(os.path.abspath(directory)).replace('\\', '/')
    with scan_state_lock:
        all_states = {}
        if os.path.exists(SCAN_STATE_FILE):
            with open(SCAN_STATE_FILE, 'r') as f:
                all_states = json.load(f)
        all_states[directory] = state
        with open(SCAN_STATE_FILE, 'w') as f:
            json.dump(all_states, f)
        log_event(f"Scan state saved for directory: {directory}")

def load_scan_state():
    if os.path.exists(SCAN_STATE_FILE):
        with open(SCAN_STATE_FILE, 'r') as f:
            return json.load(f)
    return {}

def delete_scan_state(directory):
    """
    Видаляє збережений стан сканування для заданої директорії.
    """
    directory = os.path.normpath(os.path.abspath(directory)).replace('\\', '/')
    with scan_state_lock:
        all_scan_states = load_scan_state()
        if directory in all_scan_states:
            del all_scan_states[directory]
            with open(SCAN_STATE_FILE, 'w') as f:
                json.dump(all_scan_states, f)
            log_event(f"Scan state deleted for directory: {directory}")



def scan_directory(directory, stop_event=None, resume=False):
    logging.info(f"Starting scan for directory: {directory}, resume={resume}")
    global scan_progress
    current_index = 0  # Ініціалізуємо current_index
    try:
        log_event(f"Starting scan of directory: {directory}")
        
        # Генеруємо список файлів для сканування
        files_to_scan = get_all_files(directory)
        total_files = len(files_to_scan)
        log_event(f"Total files to scan: {total_files}")
        if total_files == 0:
            with scan_progress_lock:
                scan_progress[directory] = 100
            log_event(f"No files to scan in directory: {directory}")
            return

        # Завантажуємо стан сканування, якщо відновлюємо
        with scan_state_lock:
            all_scan_states = load_scan_state()
            directory_normalized = os.path.normpath(os.path.abspath(directory)).replace('\\', '/')
            if resume and directory_normalized in all_scan_states:
                saved_state = all_scan_states[directory_normalized]
                current_index = saved_state.get('current_index', 0)
                log_event(f"Resuming scan from index {current_index} for directory: {directory}")
                if current_index >= total_files:
                    log_event(f"Saved scan state has index {current_index} >= total_files {total_files}. No scan needed.")
                    with scan_progress_lock:
                        scan_progress[directory] = 100
                    delete_scan_state(directory)
                    return
            else:
                current_index = 0
                # Якщо починаємо спочатку, видаляємо попередній стан
                if directory_normalized in all_scan_states:
                    del all_scan_states[directory_normalized]
                    with open(SCAN_STATE_FILE, 'w') as f:
                        json.dump(all_scan_states, f)
                    log_event(f"Deleted previous scan state for directory: {directory}")
        
        with app.app_context():
            # Отримуємо існуючі файли з бази даних
            existing_images = ImageMetadata.query.filter(ImageMetadata.file_path.startswith(directory)).all()
            existing_files_dict = {image.file_path: {
                'id': image.id,
                'file_hash': image.file_hash,
                'last_modified': image.last_modified.timestamp()
            } for image in existing_images}

            # Видаляємо файли з бази даних, яких більше немає в директорії
            existing_files_set = set(existing_files_dict.keys())
            current_files_set = set(files_to_scan)
            
            files_to_remove = existing_files_set - current_files_set
            
            for file_path in files_to_remove:
                image_data = existing_files_dict.get(file_path)
                if image_data:
                    image_metadata = db.session.get(ImageMetadata, image_id)
                    if image:
                        db.session.delete(image)
                        delete_embedding_from_milvus(image.id)
                        log_event(f"Removed deleted file from database: {file_path}")
            
            db.session.commit()
            log_event(f"Database updated after removing files.")
        
        # Оновлюємо прогрес сканування
        with scan_progress_lock:
            progress_percent = int((current_index / total_files) * 100) if total_files > 0 else 100
            scan_progress[directory] = progress_percent
            log_event(f"Initial progress for {directory}: {progress_percent}%")

        with app.app_context():
            # Обробка файлів
            for index in range(current_index, total_files):
                if stop_event and stop_event.is_set():
                    log_event(f"Scan of directory {directory} was stopped at index {index}.")
                    with scan_progress_lock:
                        scan_progress[directory] = -2  # -2 позначає зупинене сканування
                    # Зберігаємо стан
                    state = {'current_index': index}
                    save_scan_state(directory, state)
                    return

                file_path = files_to_scan[index]
                image_data = existing_files_dict.get(file_path)
                if image_data:
                    # Обробка існуючого файлу
                    file_mtime = os.path.getmtime(file_path)
                    db_mtime = image_data['last_modified']
                    if file_mtime > db_mtime:
                        # Файл був змінений
                        log_event(f"File modified: {file_path}")
                        process_modified_file(file_path)
                    else:
                        # Файл не змінювався, перевіряємо наявність ескізу
                        thumbnail_path = os.path.join('static', 'thumbnails', f"{image_data['id']}.png").replace('\\', '/')
                        if not os.path.exists(thumbnail_path):
                            thumbnail_path = convert_image_to_thumbnail(file_path, image_data['id'])
                            if thumbnail_path:
                                log_event(f"Thumbnail created: {thumbnail_path}")
                            else:
                                log_event(f"Failed to create thumbnail for file: {file_path}")
                        else:
                            log_event(f"Thumbnail exists for {file_path}, skipping thumbnail creation.")
                else:
                    # Новий файл
                    log_event(f"New file found: {file_path}, processing...")
                    process_new_file(file_path)

                # Оновлюємо прогрес
                current_index = index + 1
                with scan_progress_lock:
                    progress_percent = int((current_index / total_files) * 100)
                    scan_progress[directory] = progress_percent
                    log_event(f"Progress for {directory}: {progress_percent}%")
                
                # Періодично зберігаємо стан
                if current_index % 100 == 0:
                    state = {'current_index': current_index}
                    save_scan_state(directory, state)


            # Сканування завершено
            with scan_progress_lock:
                scan_progress[directory] = 100  # Завершено
                log_event(f"Finished scan of directory: {directory}")
                
            # Видаляємо стан сканування
            delete_scan_state(directory)

            # Видаляємо об'єкт stop_event та потік з глобальних словників
            with scan_stop_events_lock:
                if directory_normalized in scan_stop_events:
                    del scan_stop_events[directory_normalized]
            with scan_threads_lock:
                if directory_normalized in scan_threads:
                    del scan_threads[directory_normalized]
    except Exception as e:
        log_event(f"Error scanning directory {directory}: {e}")
        with scan_progress_lock:
            scan_progress[directory] = -1  # -1 позначає помилку
        # Зберігаємо стан при виникненні помилки
        state = {'current_index': current_index}
        save_scan_state(directory, state)
    finally:
        # Зберігаємо стан сканування у будь-якому випадку, тільки якщо current_index > 0 та сканування не завершено
        if current_index > 0 and current_index < total_files:
            state = {'current_index': current_index}
            save_scan_state(directory, state)
        elif current_index >= total_files:
            # Сканування завершено, видаляємо стан
            delete_scan_state(directory)
        else:
            # current_index ==0, видаляємо стан якщо він існує
            delete_scan_state(directory)
        
        # Оновлюємо статус сканування
        with scan_status_lock:
            if directory in scan_status:
                scan_status[directory]['active'] = False
                log_event(f"Scan status set to False for directory: {directory}")



@app.route('/get_scan_progress', methods=['GET'])
def get_scan_progress():
    response_data = {
        'scan_progress': {},
        'active_scans': []
    }

    with scan_status_lock:
        for directory, status in scan_status.items():
            progress = scan_progress.get(directory, 0)
            response_data['scan_progress'][directory] = progress
            if status.get('active'):
                response_data['active_scans'].append(directory)

    return jsonify(response_data), 200


# Маршрут для обробки збереженого стану сканування
@app.route('/handle_saved_scan_state', methods=['POST'])
def handle_saved_scan_state():
    action = request.form.get('action')
    directory = request.form.get('directory')

    logging.info(f"Received handle_saved_scan_state request: action={action}, directory={directory}")

    if not directory:
        flash("No directory provided.")
        logging.error("No directory provided in handle_saved_scan_state.")
        return redirect(url_for('index'))
    
    directory = os.path.normpath(os.path.abspath(directory)).replace('\\', '/')
    logging.info(f"Normalized directory path: {directory}")
    
    if action == 'resume':
        # Відновити сканування для конкретної директорії
        if directory not in scan_status or not scan_status[directory].get('active', False):
            scan_status[directory] = {
                'active': True,
                'stop_event': threading.Event(),
            }
            status = scan_status[directory]
            status['stop_event'].clear()
            t = threading.Thread(target=scan_directory, args=(directory, status['stop_event'], True))
            t.start()
            log_event(f"Resumed scanning for directory: {directory}")
            logging.info(f"Started scanning thread for directory: {directory}")
        else:
            log_event(f"Scan already active for directory: {directory}")
            logging.info(f"Scan already active for directory: {directory}")
        flash(f"Сканування для {directory} відновлено.")
    elif action == 'discard':
        # Видалити збережений стан для конкретної директорії
        with scan_state_lock:
            all_scan_states = load_scan_state()
            if directory in all_scan_states:
                del all_scan_states[directory]
                with open(SCAN_STATE_FILE, 'w') as f:
                    json.dump(all_scan_states, f)
                log_event(f"Saved scan state discarded for directory: {directory}")
                logging.info(f"Discarded saved scan state for directory: {directory}")
        flash(f"Збережений стан сканування для {directory} скасовано.")
    else:
        flash("Невідома дія.")
        logging.error(f"Unknown action received: {action}")

    return redirect(url_for('index'))


@app.route('/update_settings', methods=['POST'])
def update_settings():
    global recursive_scan, similarity_threshold, max_results, scan_times

    recursive_scan = 'recursive_scan' in request.form

    similarity_threshold_value = request.form.get('similarity_threshold')
    max_results_value = request.form.get('max_results')
    scan_times_values = request.form.getlist('scan_times')

    if similarity_threshold_value:
        try:
            similarity_threshold = float(similarity_threshold_value)
            if not (0 <= similarity_threshold <= 1):
                raise ValueError
        except ValueError:
            flash("Невірне значення порогу схожості. Повинно бути між 0 і 1.")
            return redirect(url_for('index'))

    if max_results_value:
        try:
            max_results = int(max_results_value)
            if max_results < 1:
                raise ValueError
        except ValueError:
            flash("Невірне значення максимальної кількості результатів.")
            return redirect(url_for('index'))

    # Обробка часу сканування з множинних полів введення
    scan_times_list = []
    if scan_times_values:
        for time_str in scan_times_values:
            if time_str:
                try:
                    datetime.strptime(time_str, "%H:%M")
                    scan_times_list.append(time_str)
                except ValueError:
                    flash(f"Невірний формат часу: {time_str}. Використовуйте формат HH:MM.")
                    return redirect(url_for('index'))

    # Оновлення значення scan_times
    scan_times = scan_times_list

    # Оновлення конфігурації
    config = {
        "similarity_threshold": similarity_threshold,
        "max_results": max_results,
        "recursive_scan": recursive_scan,
        "scan_times": scan_times  # зберігаємо навіть порожній список  
    }
    save_config(config)

    # Перепланування сканування на основі нових налаштувань
    schedule_scans()

    log_event(f"Оновлені налаштування: recursive_scan={recursive_scan}, similarity_threshold={similarity_threshold}, max_results={max_results}, scan_times={scan_times}")
    flash("Налаштування успішно оновлені.")
    return redirect(url_for('index'))

# Маршрут для отримання статистики файлів
@app.route('/file_stats', methods=['GET'])
def file_stats():
    # Загальна кількість файлів
    total_files = ImageMetadata.query.count()
    
    # Кількість файлів за форматами
    files_by_format = db.session.query(
        db.func.lower(ImageMetadata.file_type),
        db.func.count(ImageMetadata.file_type)
    ).group_by(db.func.lower(ImageMetadata.file_type)).all()
    files_by_format_dict = {fmt: count for fmt, count in files_by_format}
    
    # Кількість файлів, доданих сьогодні
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    files_today = ImageMetadata.query.filter(ImageMetadata.uploaded_at >= today_start).count()
    
    
    # Найпопулярніший формат файлів
    if files_by_format:
        most_common_format = max(files_by_format, key=lambda x: x[1])[0]
    else:
        most_common_format = None
    
    
    stats = {
        'total_files': total_files,
        'files_by_format': files_by_format_dict,
        'files_today': files_today,
        # 'files_last_week': files_last_week,  # Опціонально
        'most_common_format': most_common_format,
        # 'average_size': average_size  # Опціонально
    }
    
    return jsonify(stats), 200

@app.route('/download/<int:image_id>')
def download_image(image_id):
    image_metadata = db.session.get(ImageMetadata, image_id)
    if not image_metadata:
        abort(404)
    file_path = image_metadata.file_path
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    else:
        abort(404)


# При збереженні файлу, також зберігайте розмір
def save_file(file):
    upload_folder = './uploads'
    if not os.path.exists(upload_folder):
        os.makedirs(upload_folder)
    
    filename = secure_filename(file.filename)
    file_path = os.path.join(upload_folder, filename).replace('\\', '/')
    file.save(file_path)
    
    file_size = os.path.getsize(file_path)
    
    image_metadata = ImageMetadata(
        file_path=file_path,
        file_type=filename.split('.')[-1].lower(),
        file_size=file_size
    )
    db.session.add(image_metadata)
    db.session.commit()



# Конвертація PSD до PNG
def convert_image_to_thumbnail(file_path, image_id, output_folder='static/thumbnails', max_size=(300, 300)):
    try:
        # Перевірка наявності файлу
        if not os.path.exists(file_path):
            log_event(f"File does not exist: {file_path}")
            return None

        # Переконайтесь, що папка для мініатюр існує
        if not os.path.exists(output_folder):
            try:
                os.makedirs(output_folder, exist_ok=True)
                log_event(f"Created output folder: {output_folder}")
            except Exception as e:
                log_event(f"Failed to create output folder {output_folder}: {e}")
                return None

        # Встановлюємо шлях до мініатюри з використанням image_id
        output_path = os.path.join(output_folder, f"{image_id}.png")

        # Перевіряємо, чи вже існує мініатюра
        if os.path.exists(output_path):
            log_event(f"Thumbnail already exists for {file_path}, skipping.")
            return output_path  # Пропускаємо створення мініатюри, якщо вона вже існує

        # Обробка зображення
        ext = os.path.splitext(file_path)[1].lower()
        if ext == '.psd':
            # Обробка PSD-файлів
            try:
                psd = PSDImage.open(file_path)
                image = psd.composite()
                image = image.convert("RGB")
                log_event(f"Successfully composited PSD file: {file_path}")
            except Exception as e:
                log_event(f"Failed to process PSD file {file_path}: {e}")
                return None
        else:
            # Обробка інших форматів
            try:
                image = Image.open(file_path)
                image = image.convert("RGB")
                log_event(f"Opened image file: {file_path}")
            except Exception as e:
                log_event(f"Failed to open image file {file_path}: {e}")
                return None

        # Змінюємо розмір зображення до розміру мініатюри
        try:
            image.thumbnail(max_size, Image.Resampling.LANCZOS)
            log_event(f"Resized image to {max_size} for thumbnail: {file_path}")
        except Exception as e:
            log_event(f"Failed to resize image {file_path}: {e}")
            return None

        # Зберігаємо мініатюру
        try:
            image.save(output_path, 'PNG')
            log_event(f"Thumbnail saved at {output_path}")
            return output_path
        except Exception as e:
            log_event(f"Error saving thumbnail for {file_path}: {e}")
            return None

    except Exception as e:
        log_event(f"Unexpected error converting image to thumbnail: {e}")
        return None


# Функції для отримання ембедінгів
def get_image_embedding(image_path):
    try:
        # Перевіряємо розмір файлу
        file_size_mb = os.path.getsize(image_path) / (1024 * 1024)  # Розмір у МБ
        if file_size_mb > MAX_FILE_SIZE_MB:
            log_event(f"File {image_path} exceeds size limit of {MAX_FILE_SIZE_MB} MB, skipping.")
            return None

        # Перевіряємо, чи файл є PSD
        if image_path.lower().endswith(".psd"):
            psd = PSDImage.open(image_path)
            try:
                image = psd.composite()  # Використовуємо composite замість compose
                image = image.convert("RGB")  # Перетворюємо на RGB формат
            except Exception as e:
                log_event(f"Failed to composite PSD: {e}. Trying alternative conversion.")
                image = psd.topil()  # Альтернативний метод
        else:
            image = Image.open(image_path)
            image = image.convert("RGB")

        inputs = clip_processor(images=image, return_tensors="pt").to(device)
        log_event(f"Inputs device: {inputs['pixel_values'].device}")
        with torch.no_grad():
            embeddings = clip_model.get_image_features(**inputs).cpu().squeeze(0)
            log_event(f"Embedding generated successfully: {image_path}")
        embeddings = embeddings / embeddings.norm()
        log_event(f"Normalized image embedding norm: {embeddings.norm()}")
        return embeddings
    except UnidentifiedImageError as e:
        log_event(f"Cannot identify image file: {image_path}: {e}")
        return None
    except Exception as e:  # Додаємо 'as e' до загального except блоку
        log_event(f"Error in get_image_embedding for {image_path}: {e}")
        return None

def get_text_embedding(text):
    log_event(f"Processing text for embedding: {text}")
    
    # Отримуємо вхідні дані для моделі
    inputs = clip_processor(text=[text], return_tensors="pt", padding=True).to(device)
    log_event(f"Inputs device: {inputs['input_ids'].device}")
    
    with torch.no_grad():
        # Отримуємо текстовий ембеддинг
        text_embeddings = clip_model.get_text_features(**inputs).cpu().squeeze(0)
    
    if text_embeddings is not None:
        log_event(f"Generated text embedding of shape: {text_embeddings.shape}")
    else:
        log_event("Failed to generate text embedding.")
    
    # Нормалізація ембедінгу
    text_embeddings = text_embeddings / text_embeddings.norm()
    
    log_event(f"Normalized text embedding norm: {text_embeddings.norm()}")
    
    return text_embeddings

# Маршрут для отримання всіх зображень
@app.route('/images', methods=['GET'])
def get_images():
    images = ImageMetadata.query.all()
    result = []
    for image in images:
        result.append({
            'file_path': image.file_path,
            'file_size': image.file_size,
            'file_type': image.file_type,
            'created_at': image.created_at,
            'last_modified': image.last_modified
        })
    return jsonify(result), 200

# Функції для обробки подій
def process_new_file(file_path):
    try:
        with app.app_context():
            # Перевірка на існування файлу
            if not os.path.exists(file_path):
                log_event(f"New file {file_path} does not exist.")
                return

            # Обчислення хешу файлу
            current_hash = calculate_file_hash(file_path)
            existing_image = db.session.execute(
                db.select(ImageMetadata).filter_by(file_path=file_path)
            ).scalar_one_or_none()

            if existing_image and existing_image.file_hash == current_hash:
                log_event(f"File {file_path} already exists in database with same hash, skipping.")
                return

            # Перевірка розміру файлу
            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            log_event(f"Processing new file: {file_path}, Size: {file_size_mb} MB")
            if file_size_mb > MAX_FILE_SIZE_MB:
                log_event(f"File {file_path} exceeds size limit of {MAX_FILE_SIZE_MB} MB, skipping.")
                return

            # Отримання ембедінгу зображення
            embedding = get_image_embedding(file_path)
            if embedding is None:
                log_event(f"Failed to generate embedding for image: {file_path}")
                return

            # Нормалізація формату файлу
            ext = file_path.split('.')[-1].lower()
            if ext == 'jpeg':
                ext = 'jpg'

            # Додавання нового запису до бази даних
            new_image = ImageMetadata(
                file_path=file_path,
                file_size=os.path.getsize(file_path),
                file_type=ext,
                created_at=time.ctime(os.path.getctime(file_path)),
                last_modified=datetime.fromtimestamp(os.path.getmtime(file_path)),
                embedding=embedding.cpu().numpy(),
                file_hash=current_hash
            )
            db.session.add(new_image)
            db.session.commit()
            log_event(f"New image added to database: {file_path}")

            # Генерація мініатюри
            thumbnail_path = convert_image_to_thumbnail(file_path, new_image.id)
            if thumbnail_path:
                log_event(f"Thumbnail created: {thumbnail_path}")
            else:
                log_event(f"Failed to create thumbnail for file: {file_path}")

            # Додавання ембедінгу до Milvus
            add_embedding_to_milvus(new_image.embedding, new_image.id)
    except Exception as e:
        log_event(f"Error processing new file {file_path}: {e}")


def process_modified_file(file_path):
    try:
        with app.app_context():
            if not os.path.exists(file_path):
                log_event(f"Modified file {file_path} does not exist.")
                return

            existing_image = ImageMetadata.query.filter_by(file_path=file_path).first()
            if not existing_image:
                log_event(f"No existing image record found for modified file {file_path}")
                return

            # Отримання ембедінгу зображення
            embedding = get_image_embedding(file_path)
            if embedding is None:
                log_event(f"Failed to generate embedding for modified image: {file_path}")
                return

            # Оновлення запису у базі даних
            existing_image.embedding = embedding.cpu().numpy()
            existing_image.last_modified = datetime.fromtimestamp(os.path.getmtime(file_path))  # Корекція
            existing_image.file_size = os.path.getsize(file_path)
            existing_image.file_hash = calculate_file_hash(file_path)
            db.session.commit()
            log_event(f"Image updated in database: {file_path}")

            # Оновлення ембедінгу в Milvus
            add_embedding_to_milvus(existing_image.embedding, existing_image.id)

            # Генерація ескізу
            thumbnail_path = convert_image_to_thumbnail(file_path, existing_image.id)
            if thumbnail_path:
                log_event(f"Thumbnail updated: {thumbnail_path}")
            else:
                log_event(f"Failed to update thumbnail for file: {file_path}")

    except Exception as e:
        log_event(f"Error processing modified file {file_path}: {e}")


def process_deleted_file(file_path):
    try:
        with app.app_context():
            existing_image = ImageMetadata.query.filter_by(file_path=file_path).first()
            if existing_image:
                db.session.delete(existing_image)
                db.session.commit()
                log_event(f"Image removed from database: {file_path}")

                # Видалення ембедінгу з Milvus
                delete_embedding_from_milvus(existing_image.id)
    except Exception as e:
        log_event(f"Error processing deleted file {file_path}: {e}")

def process_deleted_directory(dir_path):
    try:
        log_event(f"Processing deletion of directory: {dir_path}")
        with app.app_context():
            normalized_dir_path = os.path.normpath(os.path.abspath(dir_path)).replace('\\', '/')
            # Видаляємо всі зображення, шляхи яких починаються з цього шляху
            images_in_directory = ImageMetadata.query.filter(
                ImageMetadata.file_path.like(f'{normalized_dir_path}%')
            ).all()

            deleted_count = len(images_in_directory)
            for image in images_in_directory:
                db.session.delete(image)
                delete_embedding_from_milvus(image.id)

            db.session.commit()
            log_event(f"Deleted {deleted_count} images from database and Milvus for directory: {normalized_dir_path}")
    except Exception as e:
        log_event(f"Error processing deleted directory {dir_path}: {e}")

def process_moved_directory(src_dir_path, dest_dir_path):
    try:
        log_event(f"Processing move from {src_dir_path} to {dest_dir_path}")
        with app.app_context():
            normalized_src_dir = os.path.normpath(os.path.abspath(src_dir_path)).replace('\\', '/')
            normalized_dest_dir = os.path.normpath(os.path.abspath(dest_dir_path)).replace('\\', '/')

            # Отримуємо всі зображення, що знаходяться в старій теці
            images_in_directory = ImageMetadata.query.filter(
                ImageMetadata.file_path.like(f'{normalized_src_dir}%')
            ).all()

            updated_count = 0
            for image in images_in_directory:
                old_path = image.file_path
                # Створюємо новий шлях
                relative_path = os.path.relpath(old_path, normalized_src_dir)
                new_path = os.path.normpath(os.path.join(normalized_dest_dir, relative_path)).replace('\\', '/')

                # Оновлюємо шлях у базі даних
                image.file_path = new_path
                if os.path.exists(new_path):
                    image.last_modified = time.ctime(os.path.getmtime(new_path))
                db.session.commit()

                # Оновлюємо ембедінг у Milvus
                add_embedding_to_milvus(image.embedding, image.id)

                log_event(f"Updated image path from {old_path} to {new_path}")
                updated_count += 1

            log_event(f"Updated {updated_count} images in database and Milvus for moved directory: {normalized_dest_dir}")
    except Exception as e:
        log_event(f"Error processing moved directory from {src_dir_path} to {dest_dir_path}: {e}")

def process_moved_file(src_path, dest_path):
    try:
        log_event(f"Processing move from {src_path} to {dest_path}")
        with app.app_context():
            normalized_src = os.path.normpath(os.path.abspath(src_path)).replace('\\', '/')
            normalized_dest = os.path.normpath(os.path.abspath(dest_path)).replace('\\', '/')

            # Знайти запис для старого шляху
            existing_image = ImageMetadata.query.filter_by(file_path=normalized_src).first()
            if existing_image:
                # Оновити шлях у базі даних
                existing_image.file_path = normalized_dest
                if os.path.exists(normalized_dest):
                    existing_image.last_modified = time.ctime(os.path.getmtime(normalized_dest))
                db.session.commit()

                # Оновити ембедінг у Milvus
                add_embedding_to_milvus(existing_image.embedding, existing_image.id)

                log_event(f"Updated image path from {normalized_src} to {normalized_dest}")
            else:
                log_event(f"No database entry found for moved file: {normalized_src}")
    except Exception as e:
        log_event(f"Error processing moved file from {src_path} to {dest_path}: {e}")

# Функція для додавання ембедінгу до Milvus
def add_embedding_to_milvus(embedding, id):
    try:
        # Конвертуємо ембедінг до одномірного списку float
        if isinstance(embedding, np.ndarray):
            embedding = embedding.astype(float).squeeze().tolist()
        elif torch.is_tensor(embedding):
            embedding = embedding.cpu().numpy().astype(float).squeeze().tolist()

        # Переконуємося, що ембедінг є списком float
        if not isinstance(embedding, list) or not all(isinstance(y, float) for y in embedding):
            raise ValueError("Embedding must be a list of float values.")
        
        # Перевірка на NaN та Inf
        if any([not np.isfinite(x) for x in embedding]):
            raise ValueError("Embedding contains NaN or Inf values.")

        # Перевірка, чи існує запис у Milvus
        existing = collection.query(expr=f"id in [{id}]", output_fields=["id"])
        if existing:
            # Якщо існує, видаляємо його перед вставкою нового
            delete_embedding_from_milvus(id)
            log_event(f"Deleted existing embedding from Milvus for ID {id} before re-inserting.")
        
        # Готуємо дані для вставки
        data = [
            {"id": id, "embedding": embedding}
        ]
        collection.insert(data)
        collection.load()
        log_event(f"Added embedding to Milvus for ID {id}.")
    except Exception as e:
        log_event(f"Failed to add embedding to Milvus: {e}")

# Функція для пошуку схожих ембедінгів у Milvus
def search_in_milvus(query_embedding, top_k=10):
    try:
        # Конвертуємо ембедінг у список float
        if isinstance(query_embedding, torch.Tensor):
            query_embedding = query_embedding.cpu().numpy().astype(float).tolist()
        elif isinstance(query_embedding, np.ndarray):
            query_embedding = query_embedding.astype(float).tolist()
        elif not isinstance(query_embedding, list):
            query_embedding = query_embedding.tolist()

        # Переконуємося, що ембедінг має довжину 512
        if len(query_embedding) != 512:
            raise ValueError("Query embedding must be of length 512.")

        # Параметри пошуку в Milvus
        search_params = {"metric_type": "IP", "params": {"nprobe": 10}}
        results = collection.search(
            [query_embedding],  # Передаємо ембедінг як список
            "embedding",
            search_params,
            limit=top_k
        )
        log_event(f"Search in Milvus completed, found {len(results[0])} results.")
        return results
    except Exception as e:
        log_event(f"Error during Milvus search: {e}")
        return []

# Функція для видалення ембедінгу з Milvus
def delete_embedding_from_milvus(id):
    try:
        # Спробуємо використати delete_entity_by_id, якщо доступно
        if hasattr(collection, 'delete_entity_by_id'):
            collection.delete_entity_by_id([id])
            log_event(f"Deleted embedding from Milvus for ID {id} using delete_entity_by_id.")
        else:
            # Використовуйте expr, якщо delete_entity_by_id недоступний
            collection.delete(expr=f"id in [{id}]")
            log_event(f"Deleted embedding from Milvus for ID {id} using expr.")
    except Exception as e:
        log_event(f"Failed to delete embedding from Milvus for ID {id}: {e}")

# Оновлення колекції Milvus після додавання нових даних
def rebuild_milvus_index():
    try:
        collection.release()  # Звільняємо колекцію

        # Перевіряємо, чи існує індекс
        if collection.has_index():
            # Отримуємо інформацію про існуючий індекс
            existing_index = collection.index()
            log_event(f"Existing index found: {existing_index.params}")

            # Видаляємо існуючий індекс
            collection.drop_index()
            log_event("Existing index dropped.")

        # Параметри нового індексу
        index_params = {
            "index_type": "IVF_FLAT",
            "metric_type": "IP",  # Або "IP", якщо ви використовуєте косинусну схожість
            "params": {"nlist": 128}
        }
        log_event(f"Creating new index with params: {index_params}")

        # Створюємо новий індекс
        collection.create_index(field_name="embedding", index_params=index_params)
        log_event("New index created.")

        collection.load()  # Завантажуємо колекцію після індексації
        log_event("Milvus index rebuilt and collection loaded successfully.")
    except Exception as e:
        log_event(f"Error rebuilding Milvus index: {e}")

# Пошук за текстом
@app.route('/search_text', methods=['POST'])
def search_text():
    data = request.get_json()
    if not data or 'text_query' not in data:
        return jsonify({"error": "Не вказано текстовий запит."}), 400

    text_query = data['text_query'].strip()
    if text_query == "":
        return jsonify({"error": "Текстовий запит не може бути порожнім."}), 400

    # Отримуємо параметри пагінації
    offset = data.get('offset', 0)
    limit = data.get('limit', max_results)  # max_results з ваших налаштувань

    # Переклад та отримання ембедінгу тексту
    # Якщо потрібно, можна додати переклад запиту на англійську
    translated_query = GoogleTranslator(source='auto', target='en').translate(text_query)
    text_embedding = get_text_embedding(translated_query)

    if text_embedding is None:
        return jsonify({"error": "Не вдалося отримати ембедінг для текстового запиту."}), 500

    # Виконання пошуку у Milvus за ембедінгом
    results = search_in_milvus(text_embedding, top_k=offset + limit)

    # Перевірка, чи є результати
    if not results:
        return jsonify({"results": []}), 200

    # Обробка результатів для серіалізації
    processed_results = []
    for result in results[0][offset:offset + limit]:  # Враховуємо зсув і ліміт
        image_id = result.id
        score = result.score
        image_metadata = db.session.get(ImageMetadata, image_id)
        if image_metadata:
            processed_results.append({
                'id': image_metadata.id,
                'file_path': image_metadata.file_path.replace('\\', '/'),
                'file_name': os.path.basename(image_metadata.file_path),
                'file_type': image_metadata.file_type,
                'similarity': score
            })

    return jsonify({"results": processed_results}), 200


# Оновлений маршрут для пошуку за зображенням
from werkzeug.utils import secure_filename

@app.route('/search_image', methods=['POST'])
def search_by_image():
    # Отримуємо параметри пагінації
    offset = int(request.form.get('offset', 0))
    limit = int(request.form.get('limit', max_results))

    if 'image' not in request.files:
        return jsonify({"error": "No image file provided."}), 400

    file = request.files['image']
    if file.filename == '':
        return jsonify({"error": "No image file selected."}), 400

    try:
        # Зберігаємо завантажене зображення
        log_event("Saving uploaded image.")
        temp_folder = 'temp_uploads'
        if not os.path.exists(temp_folder):
            os.makedirs(temp_folder)
        file_path = os.path.join(temp_folder, secure_filename(file.filename))
        file.save(file_path)
        log_event(f"Image saved to {file_path}.")

        # Перевіряємо, чи файл збережено
        if not os.path.exists(file_path):
            log_event(f"File was not saved correctly: {file_path}")
            return jsonify({"error": "Помилка при збереженні зображення."}), 500

        # Отримуємо ембедінг зображення
        log_event("Generating image embedding.")
        query_embedding = get_image_embedding(file_path)
        if query_embedding is None:
            log_event("Failed to generate image embedding.")
            return jsonify({"error": "Не вдалося отримати ембедінг для зображення."}), 500
        log_event("Image embedding generated successfully.")
    except Exception as e:
        log_event(f"Error processing uploaded image: {e}")
        return jsonify({"error": "Помилка при обробці зображення."}), 500

    # Виконуємо пошук у Milvus
    try:
        log_event("Performing search in Milvus.")
        results = search_in_milvus(query_embedding, top_k=offset + limit)
        log_event("Search completed.")
    except Exception as e:
        log_event(f"Error during search in Milvus: {e}")
        return jsonify({"error": "Помилка при пошуку в базі даних."}), 500

    # Обробка результатів з урахуванням пагінації
    similar_images = []
    if results:
        for hit in results[0][offset:offset + limit]:
            image_id = hit.id
            score = hit.score
            image_metadata = db.session.get(ImageMetadata, image_id)
            if image_metadata:
                if score >= similarity_threshold:
                    similar_images.append({
                        'id': image_metadata.id,
                        'file_path': image_metadata.file_path.replace('\\', '/'),
                        'file_name': os.path.basename(image_metadata.file_path),
                        'file_type': image_metadata.file_type,
                        'similarity': score
                    })

    # Видаляємо тимчасовий файл
    os.remove(file_path)
    log_event(f"Temporary file {file_path} deleted.")

    return jsonify({"results": similar_images}), 200




# Функція для отримання всіх файлів у директорії
def get_all_files(directory):
    files = []
    for root, dirs, filenames in os.walk(directory):
        for filename in filenames:
            if filename.lower().endswith(SUPPORTED_FORMATS):
                files.append(os.path.join(root, filename).replace('\\', '/'))
    return files

# Функція для ручного сканування
def manual_scan(selected_directories, stop_event):
    global manual_scan_active
    threads = []

    for directory in selected_directories:
        if os.path.exists(directory):
            t = threading.Thread(target=scan_directory, args=(directory, stop_event, False))
            t.start()
            threads.append(t)
        else:
            log_event(f"Directory does not exist and cannot be scanned: {directory}")

    for t in threads:
        t.join()

    with scan_progress_lock:
        manual_scan_active = False

    log_event("Manual scanning completed.")

# Додамо маршрут для ручного сканування
@app.route('/toggle_scan', methods=['POST'])
def toggle_scan_route():
    global scan_status  
    data = request.get_json()
    directory = data.get('directory')
    action = data.get('action')

    if not directory or not action:
        return jsonify({'error': 'Invalid request'}), 400

    # Переконайтеся, що директорія є в списку дозволених директорій
    directories = get_directories()
    if directory not in directories:
        return jsonify({'error': 'Directory not found'}), 404

    # Ініціалізуємо стан сканування для директорії, якщо не існує
    if directory not in scan_status:
        scan_status[directory] = {
            'active': False,
            'stop_event': threading.Event(),
        }

    status = scan_status[directory]

    if action == 'start':
        if not status['active']:
            saved_scan_states = load_scan_state()
            if directory in saved_scan_states:
                # Повідомляємо клієнту, що потрібно відновити сканування
                return jsonify({'needs_resume': True}), 200
            else:
                # Починаємо сканування без відновлення
                status['stop_event'].clear()
                status['active'] = True
                t = threading.Thread(target=scan_directory, args=(directory, status['stop_event'], False))
                t.start()
                log_event(f"Scanning started for directory: {directory}")
                return jsonify({'success': True}), 200
    elif action == 'stop':
        if status['active']:
            status['stop_event'].set()
            status['active'] = False
            log_event(f"Scanning stopped for directory: {directory}")
            return jsonify({'success': True}), 200

    return jsonify({'success': True}), 200


@app.route('/check_saved_scan_state', methods=['POST'])
def check_saved_scan_state_route():
    directory = request.json.get('directory')
    if not directory:
        return jsonify({'error': 'Invalid request'}), 400

    directory = os.path.normpath(os.path.abspath(directory)).replace('\\', '/')
    has_saved_state = False
    current_index = 0

    if os.path.exists(SCAN_STATE_FILE):
        with open(SCAN_STATE_FILE, 'r') as f:
            saved_states = json.load(f)
            if directory in saved_states:
                state = saved_states[directory]
                current_index = state.get('current_index', 0)
                if current_index > 0:
                    has_saved_state = True

    return jsonify({'has_saved_state': has_saved_state, 'current_index': current_index}), 200

# Перевірка, чи існує файл з директоріями, якщо ні - створюємо
if not os.path.exists(DIRECTORIES_FILE):
    with open(DIRECTORIES_FILE, 'w') as f:
        json.dump({'directories': []}, f)

def scan_directories():
    with scan_progress_lock:
        active = manual_scan_active
    if active:
        log_event("Manual scanning is active. Skipping scheduled scan.")
    else:
        directories = get_directories()
        for directory in directories:
            if os.path.exists(directory):
                scan_thread = threading.Thread(target=scan_directory, args=(directory,))
                scan_thread.start()
            else:
                log_event(f"Directory does not exist and cannot be scanned: {directory}")

def schedule_scans():
    scheduler.remove_all_jobs()  # Видаляємо всі існуючі завдання
    config = load_config()
    scan_times = config.get("scan_times", [])

    log_event(f"Scheduling scans with scan_times: {scan_times}")

    if not scan_times:
        # Якщо немає жодного часу сканування, зупиняємо планувальник
        if scheduler.running:
            scheduler.shutdown()
            log_event("No scan times provided. Scheduler has been shut down.")
        else:
            log_event("No scan times provided. Scheduler is not running.")
        return  # Виходимо з функції

    # Якщо є хоча б один час, додаємо його до планувальника
    for scan_time in scan_times:
        try:
            hour, minute = map(int, scan_time.strip().split(':'))
            scheduler.add_job(scan_directories, 'cron', hour=hour, minute=minute, id=f'scan_{hour}_{minute}')
            log_event(f"Scheduled scan at {hour:02d}:{minute:02d}")
        except ValueError:
            log_event(f"Invalid time format in scan_times: {scan_time}")
    
    if not scheduler.running:
        scheduler.start()  # Запускаємо планувальник, якщо він ще не запущений
        log_event("Scheduler started.")
    else:
        log_event("Scheduler is already running.")

def check_saved_scan_state():
    global has_saved_scan_state
    saved_states = load_scan_state()
    if saved_states:
        has_saved_scan_state = True
        return saved_states  # Повертаємо збережені стани
    has_saved_scan_state = False
    return {}

# Забезпечення зупинки планувальника при виході з програми
def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown()
        log_event("Scheduler has been shut down.")
    else:
        log_event("Scheduler was not running at exit.")

atexit.register(shutdown_scheduler)

# def open_browser():
    # """Функція для відкриття браузера з вказаною URL-адресою."""
    # webbrowser.open_new("http://127.0.0.1:5000/")

def ensure_index(collection, field_name, index_params):
    try:
        log_event(f"Checking for existing index on field '{field_name}' in collection '{collection.name}'")
        existing_indexes = collection.indexes
        index_exists = any(index.field_name == field_name for index in existing_indexes)
        
        if not index_exists:
            log_event(f"No existing index found on field '{field_name}'. Creating index...")
            collection.create_index(field_name=field_name, index_params=index_params)
            log_event(f"Index created for field '{field_name}' in collection '{collection.name}'")
        else:
            log_event(f"Index already exists on field '{field_name}' in collection '{collection.name}'")
    except Exception as e:
        log_event(f"Error ensuring index on field '{field_name}': {e}")



# import click
# from flask.cli import with_appcontext

# @app.cli.command("normalize-formats")
# @with_appcontext
# def normalize_formats():
    # """Нормалізувати формати файлів у базі даних."""
    # images = ImageMetadata.query.all()
    # for image in images:
        # original_ext = image.file_type
        # normalized_ext = original_ext.lower()
        # if normalized_ext == 'jpeg':
            # normalized_ext = 'jpg'
        # if image.file_type != normalized_ext:
            # image.file_type = normalized_ext
            # log_event(f"Normalized file type for {image.file_path}: {original_ext} -> {normalized_ext}")
    # db.session.commit()
    # log_event("Normalized file_type for all existing records.")
    # click.echo("Формати файлів успішно нормалізовано.")

# command in terminal - flask normalize-formats



# Запуск додатку
if __name__ == "__main__":
    with app.app_context():
        db.create_all()  # Створюємо базу даних, якщо її немає
        if not os.path.exists(DIRECTORIES_FILE):
            with open(DIRECTORIES_FILE, 'w') as f:   
                json.dump({'directories': []}, f)            
        saved_scan_states = check_saved_scan_state()

    # Підключення до Milvus
    if not connect_to_milvus(max_retries=5, delay=5):
        log_event("Exiting application due to inability to connect to Milvus.")
        exit(1)

    # Визначаємо поля та схему
    fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=512)
    ]
    schema = CollectionSchema(fields, "Колекція для зберігання ембедінгів")
    collection_name = "image_embeddings"

    # Створюємо або отримуємо колекцію
    if utility.has_collection(collection_name):
        collection = Collection(name=collection_name)
        log_event(f"Collection '{collection_name}' already exists.")
    else:
        collection = Collection(name=collection_name, schema=schema)
        log_event(f"Collection '{collection_name}' created.")

    # Переконуємося, що індекс існує
    index_params = {
        "index_type": "IVF_FLAT",
        "metric_type": "IP",
        "params": {"nlist": 128}
    }
    ensure_index(collection, "embedding", index_params)

    # Завантажуємо колекцію
    collection.load()
    log_event(f"Collection '{collection_name}' is loaded.")

    # Запускаємо планувальник сканування
    schedule_scans()

    # Запускаємо Flask-додаток
    app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=True, threaded=True)



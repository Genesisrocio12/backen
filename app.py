from flask import Flask, request, jsonify, send_file, Response
from flask_cors import CORS
import os
import zipfile
import shutil
from werkzeug.utils import secure_filename
import uuid
from datetime import datetime, timedelta
import json
from PIL import Image
import io
import subprocess
import threading
import time

try:
    from rembg import remove
    REMBG_AVAILABLE = True
    print("✓ rembg disponible - Eliminación de fondo ACTIVADA")
except ImportError:
    REMBG_AVAILABLE = False
    print("✗ rembg NO disponible - Eliminación de fondo DESACTIVADA")

app = Flask(__name__)
CORS(app)

UPLOAD_FOLDER = 'uploads'
MAX_CONTENT_LENGTH = 500 * 1024 * 1024

ALLOWED_EXTENSIONS = {
    'jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp',
    'tiff', 'tif', 'raw', 'heic', 'psd', 'zip'
}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# SISTEMA DE LIMPIEZA
def schedule_session_cleanup(session_folder, delay=3):
    def cleanup():
        time.sleep(delay)
        try:
            if os.path.exists(session_folder):
                shutil.rmtree(session_folder)
                print(f"✓ Sesión limpiada: {os.path.basename(session_folder)}")
        except Exception as e:
            print(f"✗ Error limpiando sesión: {str(e)}")
    
    threading.Thread(target=cleanup, daemon=True).start()

def cleanup_old_sessions():
    while True:
        try:
            current_time = datetime.now()
            cleaned = 0
            for session_dir in os.listdir(UPLOAD_FOLDER):
                session_path = os.path.join(UPLOAD_FOLDER, session_dir)
                if os.path.isdir(session_path):
                    dir_time = datetime.fromtimestamp(os.path.getctime(session_path))
                    if current_time - dir_time > timedelta(hours=2):
                        shutil.rmtree(session_path)
                        cleaned += 1
            
            if cleaned > 0:
                print(f"🧹 Limpieza: {cleaned} sesiones antiguas eliminadas")
        except Exception as e:
            print(f"Error en limpieza: {str(e)}")
        
        time.sleep(3600)

cleanup_thread = threading.Thread(target=cleanup_old_sessions, daemon=True)
cleanup_thread.start()

# FUNCIONES AUXILIARES
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def is_image_file(filename):
    image_extensions = {
        'jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp',
        'tiff', 'tif', 'raw', 'heic', 'psd'
    }
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in image_extensions

def extract_images_from_zip(zip_path, extract_to):
    extracted_images = []
    
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            for file_info in zip_ref.filelist:
                if not file_info.is_dir() and is_image_file(file_info.filename):
                    try:
                        zip_ref.extract(file_info, extract_to)
                        
                        original_name = os.path.basename(file_info.filename)
                        unique_name = f"{uuid.uuid4()}_{original_name}"
                        
                        old_path = os.path.join(extract_to, file_info.filename)
                        new_path = os.path.join(extract_to, unique_name)
                        
                        if os.path.dirname(file_info.filename):
                            os.makedirs(os.path.dirname(new_path), exist_ok=True)
                        
                        if os.path.exists(old_path):
                            shutil.move(old_path, new_path)
                            extracted_images.append({
                                'filename': unique_name,
                                'original_name': original_name,
                                'path': new_path,
                                'size': os.path.getsize(new_path)
                            })
                    except Exception as e:
                        print(f"Error extrayendo {file_info.filename}: {str(e)}")
                        continue
    except Exception as e:
        return [], str(e)
    
    return extracted_images, None

def optimize_with_oxipng(image_path):
    """Optimizar PNG usando oxipng"""
    try:
        result = subprocess.run(
            ['oxipng', '-o', '6', '--strip', 'safe', image_path],
            capture_output=True, text=True, timeout=30
        )
        
        if result.returncode == 0:
            return True, "Optimizado con oxipng"
        else:
            return False, f"Error oxipng: {result.stderr}"
    except Exception as e:
        return False, f"Error ejecutando oxipng: {str(e)}"

def remove_background(image_path, output_path):
    """
    FUNCIÓN CORREGIDA: Elimina el fondo usando rembg
    """
    try:
        # Guardar dimensiones originales
        with Image.open(image_path) as img:
            original_dimensions = img.size
            print(f"🎯 REMOVE_BG: Dimensiones originales: {original_dimensions}")
        
        # CASO 1: rembg NO disponible - convertir a RGBA con fondo transparente
        if not REMBG_AVAILABLE:
            print("⚠️ rembg no disponible - Conversión básica a PNG transparente")
            with Image.open(image_path) as img:
                if img.mode != 'RGBA':
                    img = img.convert('RGBA')
                img.save(output_path, 'PNG', optimize=True, compress_level=9)
                return True, "Convertido a PNG transparente (sin rembg)"
        
        # CASO 2: rembg DISPONIBLE - Eliminar fondo REAL
        print("✅ Usando rembg para eliminar fondo")
        
        # Leer imagen original
        with open(image_path, 'rb') as input_file:
            input_data = input_file.read()
        
        # APLICAR REMBG - Esta es la línea crítica
        print("🔄 Procesando con rembg...")
        output_data = remove(input_data)
        print("✓ rembg completado")
        
        # Guardar resultado temporal
        temp_output = output_path + '.temp.png'
        with open(temp_output, 'wb') as output_file:
            output_file.write(output_data)
        
        # Verificar y ajustar dimensiones si es necesario
        with Image.open(temp_output) as img:
            current_dimensions = img.size
            print(f"📐 Después de rembg: {current_dimensions}")
            
            # Si las dimensiones cambiaron, corregir
            if current_dimensions != original_dimensions:
                print(f"🔧 CORRIGIENDO: {current_dimensions} -> {original_dimensions}")
                img_resized = img.resize(original_dimensions, Image.Resampling.LANCZOS)
            else:
                img_resized = img
            
            # Asegurar modo RGBA
            if img_resized.mode != 'RGBA':
                img_resized = img_resized.convert('RGBA')
            
            # Guardar resultado final
            img_resized.save(output_path, 'PNG', optimize=True, compress_level=9)
        
        # Limpiar temporal
        if os.path.exists(temp_output):
            os.remove(temp_output)
        
        # Optimizar con oxipng si está disponible
        try:
            optimize_with_oxipng(output_path)
        except:
            pass
        
        final_size = os.path.getsize(output_path)
        print(f"✅ Fondo eliminado exitosamente - Tamaño final: {final_size // 1024}KB")
        
        return True, "Fondo eliminado"
    
    except Exception as e:
        error_msg = f"Error eliminando fondo: {str(e)}"
        print(f"❌ {error_msg}")
        return False, error_msg

def resize_image(image_path, output_path, width=None, height=None):
    """Redimensionar imagen manteniendo transparencia"""
    try:
        with Image.open(image_path) as img:
            original_dimensions = img.size
            print(f"📏 RESIZE: {original_dimensions} -> {width}x{height}")
            
            # Mantener transparencia
            if img.mode in ('RGBA', 'LA'):
                pass
            elif 'transparency' in img.info:
                img = img.convert('RGBA')
            else:
                img = img.convert('RGBA')
            
            if width and height:
                target_width = int(width)
                target_height = int(height)
                
                if target_width != original_dimensions[0] or target_height != original_dimensions[1]:
                    img_resized = img.resize((target_width, target_height), Image.Resampling.LANCZOS)
                    message = f"Redimensionado a {target_width}x{target_height}"
                    print(f"✓ {message}")
                    
                    img_resized.save(output_path, 'PNG', optimize=True, compress_level=9)
                    return True, message
                else:
                    img.save(output_path, 'PNG', optimize=True, compress_level=9)
                    return True, None
            else:
                img.save(output_path, 'PNG', optimize=True, compress_level=9)
                return True, None
    
    except Exception as e:
        return False, f"Error redimensionando: {str(e)}"

def optimize_png_only(image_path, output_path):
    """Optimizar PNG manteniendo dimensiones"""
    try:
        with Image.open(image_path) as img:
            original_dimensions = img.size
            print(f"🔧 OPTIMIZE: {original_dimensions}")
            
            if img.mode != 'RGBA':
                img = img.convert('RGBA')
            
            img.save(output_path, 'PNG', optimize=True, compress_level=9)
            
            try:
                optimize_with_oxipng(output_path)
            except:
                pass
            
            return True, f"PNG optimizado ({original_dimensions[0]}x{original_dimensions[1]})"
    
    except Exception as e:
        return False, f"Error optimizando: {str(e)}"

def create_image_preview_data(image_path):
    """Crear preview base64"""
    try:
        with Image.open(image_path) as img:
            img.thumbnail((150, 150), Image.Resampling.LANCZOS)
            
            buffer = io.BytesIO()
            if img.mode in ('RGBA', 'LA'):
                img.save(buffer, format='PNG')
            else:
                img.save(buffer, format='JPEG', quality=70)
            
            img_data = buffer.getvalue()
            import base64
            b64_data = base64.b64encode(img_data).decode()
            
            format_type = 'png' if img.mode in ('RGBA', 'LA') else 'jpeg'
            return f"data:image/{format_type};base64,{b64_data}"
    except:
        return None

def process_single_image(image_info, session_folder, options):
    """
    FUNCIÓN CORREGIDA: Procesa imagen según opciones
    """
    input_path = image_info['path']
    original_size = os.path.getsize(input_path)
    
    base_name = os.path.splitext(image_info['original_name'])[0]
    output_filename = f"{base_name}.png"
    temp_path = os.path.join(session_folder, f"temp_{uuid.uuid4()}.png")
    final_path = os.path.join(session_folder, output_filename)
    
    result = {
        'id': image_info['id'],
        'original_name': image_info['original_name'],
        'processed_name': output_filename,
        'success': False,
        'message': '',
        'operations': [],
        'original_size': original_size,
        'final_size': None,
        'size_reduction': None,
        'preview_url': None
    }
    
    current_path = input_path
    temp_files = []
    
    try:
        has_background_removal = options.get('background_removal', False)
        has_resize = options.get('resize', False)
        png_only = options.get('png_optimize_only', False)
        
        print(f"\n{'='*60}")
        print(f"📦 Procesando: {image_info['original_name']}")
        print(f"   Eliminar fondo: {has_background_removal}")
        print(f"   Redimensionar: {has_resize}")
        print(f"   Solo optimizar: {png_only}")
        print(f"{'='*60}")
        
        # FLUJO 1: Solo optimización
        if png_only and not has_background_removal and not has_resize:
            success, message = optimize_png_only(current_path, final_path)
            if success:
                result['operations'].append(message)
            else:
                result['message'] = message
                return result
        
        # FLUJO 2: Procesamiento completo
        else:
            # PASO 1: Eliminar fondo (si está activado)
            if has_background_removal:
                print("🎯 PASO 1: Eliminando fondo...")
                success, message = remove_background(current_path, temp_path)
                result['operations'].append(message)
                
                if success:
                    current_path = temp_path
                    temp_files.append(temp_path)
                    print(f"   ✓ {message}")
                else:
                    print(f"   ✗ {message}")
                    result['message'] = message
                    return result
            
            # PASO 2: Redimensionar (si está activado)
            if has_resize:
                width = options.get('width')
                height = options.get('height')
                print(f"📏 PASO 2: Redimensionando a {width}x{height}...")
                
                success, message = resize_image(current_path, final_path, width, height)
                if message:
                    result['operations'].append(message)
                    print(f"   ✓ {message}")
                
                if not success:
                    print(f"   ✗ {message}")
                    result['message'] = message
                    return result
            else:
                # Sin redimensionar, solo copiar/optimizar
                if current_path != input_path:
                    shutil.move(current_path, final_path)
                    result['operations'].append("Convertido a PNG")
                else:
                    success, message = optimize_png_only(current_path, final_path)
                    result['operations'].append(message)
        
        # Calcular estadísticas finales
        if os.path.exists(final_path):
            final_size = os.path.getsize(final_path)
            size_reduction = ((original_size - final_size) / original_size) * 100
            
            preview_url = create_image_preview_data(final_path)
            
            result['success'] = True
            result['message'] = 'Procesado exitosamente'
            result['final_size'] = final_size
            result['size_reduction'] = size_reduction
            result['path'] = final_path
            result['preview_url'] = preview_url
            
            print(f"✅ COMPLETADO: {original_size//1024}KB -> {final_size//1024}KB ({size_reduction:+.1f}%)")
        else:
            result['message'] = 'Error: archivo final no encontrado'
            print("❌ ERROR: Archivo final no existe")
    
    except Exception as e:
        result['message'] = f'Error: {str(e)}'
        print(f"❌ EXCEPCIÓN: {str(e)}")
    
    finally:
        # Limpiar archivos temporales
        for temp_file in temp_files:
            if os.path.exists(temp_file) and temp_file != final_path:
                try:
                    os.remove(temp_file)
                except:
                    pass
    
    return result

# ENDPOINTS
@app.route('/api/health', methods=['GET'])
def health_check():
    oxipng_available = False
    try:
        result = subprocess.run(['oxipng', '--version'],
                              capture_output=True, text=True, timeout=5)
        oxipng_available = result.returncode == 0
    except:
        pass
    
    return jsonify({
        'status': 'ok',
        'message': 'ImageProcessor Backend funcionando',
        'rembg_available': REMBG_AVAILABLE,
        'oxipng_available': oxipng_available,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/upload', methods=['POST'])
def upload_files():
    if 'files' not in request.files:
        return jsonify({'error': 'No se encontraron archivos'}), 400
    
    files = request.files.getlist('files')
    
    if not files or all(f.filename == '' for f in files):
        return jsonify({'error': 'No se seleccionaron archivos'}), 400
    
    session_id = str(uuid.uuid4())
    session_folder = os.path.join(UPLOAD_FOLDER, session_id)
    os.makedirs(session_folder, exist_ok=True)
    
    uploaded_files = []
    errors = []
    zip_count = 0
    direct_count = 0
    
    try:
        for file in files:
            if file and file.filename and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                unique_filename = f"{uuid.uuid4()}_{filename}"
                file_path = os.path.join(session_folder, unique_filename)
                
                file.save(file_path)
                
                if filename.lower().endswith('.zip'):
                    zip_count += 1
                    extracted_images, error = extract_images_from_zip(file_path, session_folder)
                    
                    if error:
                        errors.append(f"Error extrayendo {filename}: {error}")
                        continue
                    
                    if not extracted_images:
                        errors.append(f"No se encontraron imágenes en {filename}")
                        continue
                    
                    for img in extracted_images:
                        uploaded_files.append({
                            'id': str(uuid.uuid4()),
                            'filename': img['filename'],
                            'original_name': img['original_name'],
                            'type': 'image',
                            'source': 'zip',
                            'size': img['size'],
                            'path': img['path']
                        })
                    os.remove(file_path)
                
                else:
                    direct_count += 1
                    file_size = os.path.getsize(file_path)
                    uploaded_files.append({
                        'id': str(uuid.uuid4()),
                        'filename': unique_filename,
                        'original_name': filename,
                        'type': 'image',
                        'source': 'direct',
                        'size': file_size,
                        'path': file_path
                    })
            else:
                errors.append(f"Archivo no permitido: {file.filename if file.filename else 'sin nombre'}")
    
    except Exception as e:
        if os.path.exists(session_folder):
            shutil.rmtree(session_folder)
        return jsonify({'error': f'Error procesando archivos: {str(e)}'}), 500
    
    if not uploaded_files:
        if os.path.exists(session_folder):
            shutil.rmtree(session_folder)
        return jsonify({'error': 'No se encontraron archivos válidos'}), 400
    
    upload_type = 'single' if direct_count == 1 and zip_count == 0 and len(uploaded_files) == 1 else 'multiple'
    
    session_metadata = {
        'session_id': session_id,
        'created_at': datetime.now().isoformat(),
        'files': uploaded_files,
        'errors': errors,
        'processed': False,
        'upload_type': upload_type,
        'stats': {
            'direct_files': direct_count,
            'zip_files': zip_count,
            'total_images': len(uploaded_files)
        }
    }
    
    metadata_path = os.path.join(session_folder, 'metadata.json')
    with open(metadata_path, 'w', encoding='utf-8') as f:
        json.dump(session_metadata, f, indent=2, ensure_ascii=False)
    
    print(f"✓ Subidos {len(uploaded_files)} archivos en sesión {session_id}")
    
    return jsonify({
        'success': True,
        'session_id': session_id,
        'uploaded_files': len(uploaded_files),
        'files': uploaded_files,
        'errors': errors,
        'upload_type': upload_type
    })

@app.route('/api/process', methods=['POST'])
def process_images():
    data = request.get_json()
    
    if not data or 'session_id' not in data:
        return jsonify({'error': 'session_id requerido'}), 400
    
    session_id = data['session_id']
    session_folder = os.path.join(UPLOAD_FOLDER, session_id)
    metadata_path = os.path.join(session_folder, 'metadata.json')
    
    if not os.path.exists(metadata_path):
        return jsonify({'error': 'Sesión no encontrada'}), 404
    
    try:
        with open(metadata_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
    except Exception as e:
        return jsonify({'error': f'Error leyendo metadatos: {str(e)}'}), 500
    
    background_removal = data.get('background_removal', False)
    resize = data.get('resize', False)
    width = data.get('width')
    height = data.get('height')
    
    actual_resize = resize and width and height and int(width) > 0 and int(height) > 0
    
    options = {
        'background_removal': background_removal,
        'resize': actual_resize,
        'width': int(width) if width and str(width).strip() != '' else None,
        'height': int(height) if height and str(height).strip() != '' else None,
        'png_optimize_only': False
    }
    
    if not background_removal and not actual_resize:
        options['png_optimize_only'] = True
        print(f"📦 Modo: Solo optimización para {len(metadata['files'])} imágenes")
    else:
        print(f"\n{'='*70}")
        print(f"📦 INICIANDO PROCESAMIENTO")
        print(f"   Imágenes: {len(metadata['files'])}")
        print(f"   Eliminar fondo: {'SÍ' if background_removal else 'NO'}")
        print(f"   Redimensionar: {'SÍ' if actual_resize else 'NO'}")
        if actual_resize:
            print(f"   Dimensiones: {options['width']}x{options['height']}")
        print(f"{'='*70}\n")
    
    processed_results = []
    
    for file_info in metadata['files']:
        if not os.path.exists(file_info['path']):
            processed_results.append({
                'id': file_info['id'],
                'original_name': file_info['original_name'],
                'success': False,
                'message': 'Archivo no encontrado'
            })
            continue
        
        result = process_single_image(file_info, session_folder, options)
        processed_results.append(result)
    
    metadata['processed'] = True
    metadata['processed_at'] = datetime.now().isoformat()
    metadata['processing_options'] = options
    metadata['results'] = processed_results
    
    try:
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Error guardando metadatos: {str(e)}")
    
    successful = sum(1 for r in processed_results if r['success'])
    failed = len(processed_results) - successful
    
    print(f"\n{'='*70}")
    print(f"✅ PROCESAMIENTO COMPLETADO")
    print(f"   Exitosos: {successful}")
    print(f"   Fallidos: {failed}")
    print(f"{'='*70}\n")
    
    return jsonify({
        'success': True,
        'message': f'Procesamiento completado: {successful} exitosos, {failed} fallidos',
        'session_id': session_id,
        'results': processed_results,
        'stats': {
            'total': len(processed_results),
            'successful': successful,
            'failed': failed
        }
    })

@app.route('/api/download/<session_id>', methods=['GET'])
def download_processed(session_id):
    session_folder = os.path.join(UPLOAD_FOLDER, session_id)
    metadata_path = os.path.join(session_folder, 'metadata.json')
    
    if not os.path.exists(metadata_path):
        return jsonify({'error': 'Sesión no encontrada'}), 404
    
    try:
        with open(metadata_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
    except Exception as e:
        return jsonify({'error': f'Error leyendo sesión: {str(e)}'}), 500
    
    if not metadata.get('processed', False):
        return jsonify({'error': 'Imágenes no procesadas'}), 400
    
    successful_files = [r for r in metadata.get('results', []) if r.get('success', False)]
    
    if not successful_files:
        schedule_session_cleanup(session_folder, delay=1)
        return jsonify({'error': 'No hay archivos procesados'}), 400
    
    # Descarga individual
    if len(successful_files) == 1:
        result = successful_files[0]
        file_path = result.get('path')
        
        if not file_path or not os.path.exists(file_path):
            schedule_session_cleanup(session_folder, delay=1)
            return jsonify({'error': 'Archivo no encontrado'}), 404
        
        try:
            with open(file_path, 'rb') as f:
                file_data = f.read()
            
            schedule_session_cleanup(session_folder, delay=3)
            
            response = Response(
                file_data,
                mimetype='image/png',
                headers={
                    'Content-Disposition': f'attachment; filename="{result["processed_name"]}"',
                    'Content-Type': 'image/png',
                    'Content-Length': str(len(file_data))
                }
            )
            
            print(f"📥 Descarga individual: {result['processed_name']}")
            return response
        
        except Exception as e:
            schedule_session_cleanup(session_folder, delay=1)
            return jsonify({'error': f'Error: {str(e)}'}), 500
    
    # Descarga ZIP múltiple
    zip_filename = f"imagenes_procesadas_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    
    try:
        from io import BytesIO
        zip_buffer = BytesIO()
        
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zipf:
            for result in successful_files:
                file_path = result.get('path')
                if file_path and os.path.exists(file_path):
                    zipf.write(file_path, result['processed_name'])
        
        zip_buffer.seek(0)
        zip_data = zip_buffer.getvalue()
        
        schedule_session_cleanup(session_folder, delay=3)
        
        response = Response(
            zip_data,
            mimetype='application/zip',
            headers={
                'Content-Disposition': f'attachment; filename="{zip_filename}"',
                'Content-Type': 'application/zip',
                'Content-Length': str(len(zip_data))
            }
        )
        
        print(f"📥 Descarga ZIP: {zip_filename} ({len(successful_files)} imágenes)")
        return response
    
    except Exception as e:
        schedule_session_cleanup(session_folder, delay=1)
        return jsonify({'error': f'Error creando ZIP: {str(e)}'}), 500

@app.route('/api/session/<session_id>', methods=['GET'])
def get_session_info(session_id):
    session_folder = os.path.join(UPLOAD_FOLDER, session_id)
    metadata_path = os.path.join(session_folder, 'metadata.json')
    
    if not os.path.exists(metadata_path):
        return jsonify({'error': 'Sesión no encontrada'}), 404
    
    try:
        with open(metadata_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
        return jsonify(metadata)
    except Exception as e:
        return jsonify({'error': f'Error leyendo sesión: {str(e)}'}), 500

@app.route('/api/preview/<filename>', methods=['GET'])
def get_image_preview(filename):
    found_path = None
    
    try:
        for session_dir in os.listdir(UPLOAD_FOLDER):
            session_path = os.path.join(UPLOAD_FOLDER, session_dir)
            if os.path.isdir(session_path):
                for file in os.listdir(session_path):
                    if file == filename:
                        found_path = os.path.join(session_path, file)
                        break
                if found_path:
                    break
    except Exception as e:
        return jsonify({'error': f'Error buscando archivo: {str(e)}'}), 500
    
    if not found_path or not os.path.exists(found_path):
        return jsonify({'error': 'Archivo no encontrado'}), 404
    
    try:
        with Image.open(found_path) as img:
            thumbnail = img.copy()
            thumbnail.thumbnail((300, 300), Image.Resampling.LANCZOS)
            
            img_buffer = io.BytesIO()
            
            format = img.format if img.format else 'PNG'
            if format in ['JPEG', 'JPG']:
                if thumbnail.mode in ('RGBA', 'LA'):
                    thumbnail = thumbnail.convert('RGB')
                thumbnail.save(img_buffer, format='JPEG', quality=85, optimize=True)
                mimetype = 'image/jpeg'
            else:
                thumbnail.save(img_buffer, format='PNG', optimize=True)
                mimetype = 'image/png'
            
            img_buffer.seek(0)
            
            return send_file(
                img_buffer,
                mimetype=mimetype,
                as_attachment=False
            )
    
    except Exception as e:
        return jsonify({'error': f'Error procesando imagen: {str(e)}'}), 500

@app.route('/api/dimensions/<session_id>', methods=['GET'])
def get_image_dimensions(session_id):
    session_folder = os.path.join(UPLOAD_FOLDER, session_id)
    metadata_path = os.path.join(session_folder, 'metadata.json')
    
    if not os.path.exists(metadata_path):
        return jsonify({'error': 'Sesión no encontrada'}), 404
    
    try:
        with open(metadata_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
    except Exception as e:
        return jsonify({'error': f'Error leyendo sesión: {str(e)}'}), 500
    
    files = metadata.get('files', [])
    if not files:
        return jsonify({'error': 'No hay archivos en la sesión'}), 404
    
    first_file = files[0]
    image_path = first_file.get('path')
    
    if not image_path or not os.path.exists(image_path):
        return jsonify({'error': 'Archivo de imagen no encontrado'}), 404
    
    try:
        with Image.open(image_path) as img:
            dimensions = {
                'width': img.width,
                'height': img.height,
                'aspect_ratio': img.width / img.height,
                'format': img.format,
                'mode': img.mode,
                'size_bytes': os.path.getsize(image_path),
                'filename': first_file.get('original_name', 'unknown')
            }
            
            return jsonify({
                'success': True,
                'dimensions': dimensions
            })
    
    except Exception as e:
        return jsonify({'error': f'Error obteniendo dimensiones: {str(e)}'}), 500

@app.route('/api/cleanup/<session_id>', methods=['DELETE'])
def manual_cleanup(session_id):
    """Limpiar manualmente una sesión específica"""
    session_folder = os.path.join(UPLOAD_FOLDER, session_id)
    
    if not os.path.exists(session_folder):
        return jsonify({'error': 'Sesión no encontrada'}), 404
    
    try:
        shutil.rmtree(session_folder)
        return jsonify({
            'success': True,
            'message': f'Sesión {session_id} eliminada'
        })
    except Exception as e:
        return jsonify({'error': f'Error eliminando sesión: {str(e)}'}), 500

@app.route('/api/cleanup/all', methods=['DELETE'])
def cleanup_all_sessions():
    """Limpiar TODAS las sesiones - SOLO DESARROLLO"""
    try:
        cleaned = 0
        for session_dir in os.listdir(UPLOAD_FOLDER):
            session_path = os.path.join(UPLOAD_FOLDER, session_dir)
            if os.path.isdir(session_path):
                shutil.rmtree(session_path)
                cleaned += 1
        
        return jsonify({
            'success': True,
            'message': f'{cleaned} sesiones eliminadas',
            'cleaned_count': cleaned
        })
    except Exception as e:
        return jsonify({'error': f'Error en limpieza masiva: {str(e)}'}), 500

if __name__ == '__main__':
    print("=" * 70)
    print(" 🚀 ImageProcessor Backend - INICIANDO")
    print("=" * 70)
    print(f" 📁 Uploads: {os.path.abspath(UPLOAD_FOLDER)}")
    print(f" 📎 Formatos: {', '.join(sorted(ALLOWED_EXTENSIONS))}")
    print(f" 🔗 Servidor: http://localhost:5000")
    print(f" 💚 Health: http://localhost:5000/api/health")
    print(f" 🎨 rembg: {'DISPONIBLE ✓' if REMBG_AVAILABLE else 'NO DISPONIBLE ✗'}")
    print("=" * 70)
    
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
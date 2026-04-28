from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.files.storage import default_storage
from django.conf import settings
import os
from .services.excel import get_blending_data, EXCEL_PATH

# We use the path defined in excel.py for consistency
# But ideally we should move this constant to settings or a common config
# references: excel.py defines EXCEL_PATH

@csrf_exempt
def vault_download(request):
    """
    Download the current blending source file.
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    file_path = EXCEL_PATH
        
    if not os.path.exists(file_path):
         return JsonResponse({'error': 'File not found'}, status=404)

    try:
        with open(file_path, 'rb') as f:
            response = HttpResponse(f.read(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            filename = os.path.basename(file_path)
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

@csrf_exempt
def vault_upload(request):
    """
    Upload and overwrite the blending source file.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    if 'file' not in request.FILES:
        return JsonResponse({'error': 'No file provided'}, status=400)

    uploaded_file = request.FILES['file']
    
    if not uploaded_file.name.endswith('.xlsx'):
        return JsonResponse({'error': 'Invalid file type. Must be .xlsx'}, status=400)

    save_path = EXCEL_PATH
    
    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        
        with open(save_path, 'wb+') as destination:
            for chunk in uploaded_file.chunks():
                destination.write(chunk)

        # Trigger Reload by calling the service (it doesn't have explicit cache clear in current simple version, 
        # but subsequent reads will read file from disk)
        # We can just call it to ensure it verifies the file
        get_blending_data()
        
        return JsonResponse({'success': True, 'message': 'File updated successfully'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)

@csrf_exempt
def vault_status(request):
    """
    Returns metadata about the current source file.
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    file_path = EXCEL_PATH
    
    if not os.path.exists(file_path):
        return JsonResponse({'exists': False})

    try:
        stats = os.stat(file_path)
        return JsonResponse({
            'exists': True,
            'name': os.path.basename(file_path),
            'size': stats.st_size,
            'last_modified': stats.st_mtime
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

@csrf_exempt
def vault_styles(request):
    # Stub for compatibility if needed, but we removed it from frontend
    return JsonResponse({})

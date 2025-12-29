import cv2
import pytesseract
from PIL import Image
import re
import numpy as np
from ultralytics import YOLO  
import os

MODEL_PATH = os.path.join(os.path.dirname(__file__), 'models', 'best.pt')
try:
    model = YOLO(MODEL_PATH)
    print("--- [System] YOLOv8 model loaded successfully! ---")
except Exception as e:
    print(f"--- [System] Model loading failed: {e} ---")
    model = None

# --- 1. OCR Function (Keep! Responsible for reading ID) ---
def analyze_image_with_ocr(image_path):
    try:
        img = Image.open(image_path)
        text = pytesseract.image_to_string(img)
        if not text: return "OCR_No_Text"
        cleaned_text = " ".join(text.split()).upper()
        # Look for P-xxxx or SPX... or 10+ digit numbers
        match = re.search(r'(P-[\d-]+|SPX[A-Z0-9]+|\d{10,})', cleaned_text, re.IGNORECASE)
        return match.group(0).upper() if match else "OCR_ID_Not_Found"
    except:
        return "Error"

# --- 2. YOLO Function (Upgraded: Saves the image!) ---
def estimate_dimensions_yolo(image_path):
    if not model:
        return 0.1, "Model Error"

    # Run inference
    results = model(image_path)
    
    # --- New Feature: Save image with bounding boxes ---
    # This will append "_predicted.jpg" to the original filename and save in the same folder
    for r in results:
        im_array = r.plot()  # Plot boxes on the image
        # Generate new filename: e.g., "uploads/123.jpg" -> "uploads/123_predicted.jpg"
        save_path = image_path.replace(".jpg", "_predicted.jpg").replace(".jpeg", "_predicted.jpg").replace(".png", "_predicted.png")
        cv2.imwrite(save_path, im_array) # Save image
        print(f"--- [AI] Predicted image saved to: {save_path} ---")
    # --------------------------------

    # Get detected boxes
    parcel_box = None
    ruler_box = None

    for r in results:
        boxes = r.boxes
        for box in boxes:
            cls = int(box.cls[0]) # Class ID
            # box.xywh returns [x_center, y_center, width, height]
            w = float(box.xywh[0][2])
            h = float(box.xywh[0][3])
            
            # Take the longest side as "length" (pixels)
            pixel_length = max(w, h)

            if cls == 0: # Parcel (Assuming 0 is Parcel)
                parcel_box = pixel_length
                p_w, p_h = w, h
            elif cls == 1: # Ruler (Assuming 1 is Ruler)
                ruler_box = pixel_length

    # --- Core Algorithm: Ratio Conversion ---
    
    # Case A: No parcel found
    if parcel_box is None:
        return 0.05, "No Parcel Found"

    # Case B: Parcel found, but no ruler found
    if ruler_box is None:
        print("--- [AI] Warning: No ruler detected, using default scale ---")
        scale = 0.05 
    else:
        # Case C: Perfect!
        REAL_RULER_LENGTH_CM = 30.0 
        scale = REAL_RULER_LENGTH_CM / ruler_box
    
    # Calculate real parcel dimensions (cm)
    real_w_cm = p_w * scale
    real_h_cm = p_h * scale
    real_d_cm = min(real_w_cm, real_h_cm) * 0.5 

    # Calculate volume (m3)
    volume_m3 = (real_w_cm * real_h_cm * real_d_cm) / 1_000_000
    
    dims_str = f"{int(real_w_cm)}*{int(real_h_cm)}*{int(real_d_cm)}cm"

    return round(volume_m3, 4), dims_str

# --- 3. Main Function ---
def analyze_parcel_image(image_path):
    print(f"--- [AI Analyzer] Analyzing: {image_path} ---")
    
    # Parallel work: One reads text, one looks at image
    parcel_id = analyze_image_with_ocr(image_path)
    volume, dims = estimate_dimensions_yolo(image_path)
    
    # Estimate weight
    weight = round(volume * 30, 2) 

    final_results = {
        "external_id": parcel_id,
        "dimensions": dims,
        "weight": weight,
        "volume": volume
    }
    
    print(f"--- [AI Result] {final_results} ---")
    return final_results
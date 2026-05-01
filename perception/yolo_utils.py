"""
yolo_utils.py (优化版)
支持自定义推理尺寸以提升边缘端速度
"""

from ultralytics import YOLO
import cv2
from typing import List, Dict, Any

_model = None

def load_yolo_model(model_name: str = "yolov8n.pt") -> YOLO:
    global _model
    if _model is None:
        print(f"正在加载YOLO模型: {model_name} ...")
        _model = YOLO(model_name)
        print("模型加载完成")
    return _model

def draw_boxes(frame, objects):
    for obj in objects:
        x1, y1, x2, y2 = obj["bbox"]
        cls = obj["class"]
        conf = obj["confidence"]
        label = f"{cls} {conf:.2f}"

        # 画框
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)

        # 画文字
        cv2.putText(frame, label,
                    (x1, y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 255, 0),
                    2)

    return frame

def detect_objects(frame: cv2.typing.MatLike, 
                   conf_threshold: float = 0.5,
                   iou_threshold: float = 0.45,
                   imgsz: int = 320) -> List[Dict[str, Any]]: # 新增 imgsz 默认 320
    """
    对单帧图像进行物体检测
    """
    model = load_yolo_model()
    
    # 增加 imgsz 参数，对于树莓派等设备建议使用 320 或 416
    results = model(frame, conf=conf_threshold, iou=iou_threshold, verbose=False, imgsz=imgsz)
    
    objects = []
    if len(results) > 0:
        boxes = results[0].boxes
        if boxes is not None:
            for box in boxes:
                cls_id = int(box.cls[0])
                class_name = model.names[cls_id]
                confidence = float(box.conf[0])
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                objects.append({
                    "class": class_name,
                    "confidence": confidence,
                    "bbox": [x1, y1, x2, y2]
                })
    return objects
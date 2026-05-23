import os
import cv2
import numpy as np
from typing import List, Dict, Any


COCO_CLASSES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
    "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier",
    "toothbrush"
]


_model = None
_onnx_session = None
_onnx_io = None
_backend = None


def load_yolo_model(model_name: str = "yolov8n",
                    backend: str = "auto",
                    onnx_path: str = None):
    global _model, _onnx_session, _onnx_io, _backend

    if backend == "onnx":
        path = onnx_path or f"{model_name}.onnx"
        if not os.path.exists(path):
            raise FileNotFoundError(f"[ONNX] Model not found: {path}")

        try:
            import onnxruntime as ort
        except ImportError:
            raise ImportError("[ONNX] onnxruntime not installed. pip install onnxruntime")

        sess_options = ort.SessionOptions()
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        _onnx_session = ort.InferenceSession(path, sess_options, providers=['CPUExecutionProvider'])
        _onnx_io = {
            "input": _onnx_session.get_inputs()[0].name,
            "output": _onnx_session.get_outputs()[0].name
        }
        _backend = "onnx"
        print(f"[ONNX] Model loaded: {path}")
        return _onnx_session

    elif backend == "pt":
        from ultralytics import YOLO

        pt_path = f"{model_name}.pt"
        _model = YOLO(pt_path)
        _backend = "pt"
        print(f"[PT] Model loaded: {pt_path}")
        return _model

    elif backend == "auto":
        auto_onnx = onnx_path or f"{model_name}.onnx"
        if os.path.exists(auto_onnx):
            try:
                return load_yolo_model(model_name, "onnx", auto_onnx)
            except Exception as e:
                print(f"[AUTO] ONNX load failed ({e}), falling back to PT")
        return load_yolo_model(model_name, "pt")

    else:
        raise ValueError(f"Unknown backend: {backend}")


def _letterbox(img: np.ndarray, new_shape: int = 640,
               color: tuple = (114, 114, 114)) -> tuple:
    shape = img.shape[:2]
    if isinstance(new_shape, int):
        new_shape = (new_shape, new_shape)

    r = min(new_shape[0] / shape[0], new_shape[1] / shape[1])
    new_unpad = (int(round(shape[1] * r)), int(round(shape[0] * r)))

    dw, dh = new_shape[1] - new_unpad[0], new_shape[0] - new_unpad[1]
    dw, dh = dw / 2.0, dh / 2.0

    if shape[::-1] != new_unpad:
        img = cv2.resize(img, new_unpad, interpolation=cv2.INTER_LINEAR)

    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    img = cv2.copyMakeBorder(img, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color)

    return img, r, (dw, dh)


def _preprocess_onnx(frame: np.ndarray, imgsz: int = 320) -> tuple:
    img, ratio, pad = _letterbox(frame, imgsz)
    img = img[:, :, ::-1]  # BGR -> RGB
    img = img.astype(np.float32) / 255.0
    img = img.transpose(2, 0, 1)  # HWC -> CHW
    blob = np.expand_dims(img, axis=0)  # (1, 3, H, W)
    return blob, ratio, pad


def _nms(boxes: np.ndarray, scores: np.ndarray,
         conf_threshold: float = 0.5,
         iou_threshold: float = 0.45) -> List[int]:
    mask = scores > conf_threshold
    if not mask.any():
        return []
    boxes = boxes[mask]
    scores = scores[mask]
    indices = np.arange(len(mask))[mask]

    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]

    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(i)
        if order.size == 1:
            break

        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])

        w = np.maximum(0.0, xx2 - xx1)
        h = np.maximum(0.0, yy2 - yy1)
        inter = w * h

        iou = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[1:][iou <= iou_threshold]

    return [int(indices[i]) for i in keep]


def _postprocess_onnx(output: np.ndarray,
                      conf_threshold: float,
                      iou_threshold: float,
                      orig_shape: tuple,
                      ratio: float,
                      pad: tuple) -> List[Dict[str, Any]]:
    predictions = output[0].T  # (N, 84)
    raw_boxes = predictions[:, :4]
    cx, cy, w, h = raw_boxes[:, 0], raw_boxes[:, 1], raw_boxes[:, 2], raw_boxes[:, 3]
    boxes = np.stack([
        cx - w / 2, cy - h / 2,
        cx + w / 2, cy + h / 2
    ], axis=1)
    scores = predictions[:, 4:]

    max_scores = scores.max(axis=1)
    class_ids = scores.argmax(axis=1)

    gain = 1.0 / ratio
    boxes[:, [0, 2]] -= pad[0]
    boxes[:, [1, 3]] -= pad[1]
    boxes[:, [0, 2]] *= gain
    boxes[:, [1, 3]] *= gain

    h, w = orig_shape
    boxes[:, 0] = np.clip(boxes[:, 0], 0, w)
    boxes[:, 2] = np.clip(boxes[:, 2], 0, w)
    boxes[:, 1] = np.clip(boxes[:, 1], 0, h)
    boxes[:, 3] = np.clip(boxes[:, 3], 0, h)

    keep_indices = _nms(boxes, max_scores, conf_threshold, iou_threshold)

    results = []
    for idx in keep_indices:
        cls_id = int(class_ids[idx])
        if cls_id < len(COCO_CLASSES):
            results.append({
                "class": COCO_CLASSES[cls_id],
                "confidence": float(max_scores[idx]),
                "bbox": [int(boxes[idx, 0]), int(boxes[idx, 1]),
                         int(boxes[idx, 2]), int(boxes[idx, 3])]
            })
    return results


def detect_objects(frame: cv2.typing.MatLike,
                   conf_threshold: float = 0.5,
                   iou_threshold: float = 0.45,
                   imgsz: int = 320) -> List[Dict[str, Any]]:
    global _model, _onnx_session, _onnx_io, _backend

    if _backend == "onnx":
        if _onnx_session is None:
            raise RuntimeError("[ONNX] Model not loaded. Call load_yolo_model() first.")

        blob, ratio, pad = _preprocess_onnx(frame, imgsz)
        outputs = _onnx_session.run([_onnx_io["output"]], {_onnx_io["input"]: blob})
        return _postprocess_onnx(outputs[0], conf_threshold, iou_threshold,
                                 frame.shape[:2], ratio, pad)

    else:
        if _model is None:
            raise RuntimeError("[PT] Model not loaded. Call load_yolo_model() first.")

        results = _model(frame, conf=conf_threshold, iou=iou_threshold,
                         verbose=False, imgsz=imgsz)

        objects = []
        if len(results) > 0:
            boxes = results[0].boxes
            if boxes is not None:
                for box in boxes:
                    cls_id = int(box.cls[0])
                    class_name = _model.names[cls_id]
                    confidence = float(box.conf[0])
                    x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                    objects.append({
                        "class": class_name,
                        "confidence": confidence,
                        "bbox": [x1, y1, x2, y2]
                    })
        return objects


def draw_boxes(frame, objects):
    for obj in objects:
        x1, y1, x2, y2 = obj["bbox"]
        cls = obj["class"]
        conf = obj["confidence"]
        label = f"{cls} {conf:.2f}"

        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(frame, label,
                    (x1, y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 255, 0),
                    2)
    return frame

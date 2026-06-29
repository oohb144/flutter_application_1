"""
亚博智能 K230 人脸识别智能系统 - 人脸识别模块（CanMV）

由 maix-dostudy (MaixPy4 nn.FaceRecognizer) 移植。
K230 无 nn.FaceRecognizer 封装，需用 AIBase + 双 kmodel + 手工对齐 + 余弦匹配实现：

流程：
  整帧 RGB888 -> face_detection_320.kmodel -> aidemo.face_det_post_process
              -> 5 关键点 -> 3 点仿射对齐到 112x112
              -> face_recognition.kmodel -> 128 维特征 -> L2 归一化
              -> 与人脸库余弦相似度匹配 (dot/2+0.5)

人脸库：每标签一个 .bin 文件（ulab.numpy tofile/fromfile），存于 FACES_DB_DIR。

对外接口与参考 face_detector.py 对齐，使 main.py 改动最小。

⚠ 上板注意（本地无 K230 示例可参考，以下可能需微调）：
  - aidemo.face_det_post_process 返回的具体字段顺序
  - face_recognition.kmodel 的输出维度与归一化方式
  - ai2d.affine 的矩阵格式（这里用 2x3 即 [a,b,c,d,e,f]）
  若仿射对齐不稳定，set use_alignment=False 降级为 crop+resize。
"""

import os
import math
import gc
import ulab.numpy as np
import nncase_runtime as nn
import aidemo
from libs.AIBase import AIBase
from libs.AI2D import Ai2d
import config


def _align_up(x, align=16):
    return (x + align - 1) & ~(align - 1)


def _path_exists(p):
    """os.path.exists 的 CanMV 兼容实现（os.path 在 CanMV 不可用）"""
    try:
        os.stat(p)
        return True
    except Exception:
        return False


def _ensure_dir(path):
    """确保目录存在（CanMV 的 os 通常只有 mkdir 单层，父目录需已存在）"""
    if _path_exists(path):
        return
    try:
        os.mkdir(path)
    except Exception as e:
        # 父目录不存在时尝试逐级创建
        try:
            parts = path.rstrip('/').split('/')
            cur = ''
            for part in parts:
                if part == '':
                    continue
                cur = cur + '/' + part
                if not _path_exists(cur):
                    os.mkdir(cur)
        except Exception as e2:
            print(f"[人脸] 创建目录失败 {path}: {e2}")


# Arcface 标准 5 关键点（112x112）：左眼、右眼、鼻、左嘴角、右嘴角
# 对齐只用前 3 点（左眼、右眼、鼻）解 2x3 仿射矩阵
_ARC_STD_3 = [
    (38.2946, 51.6963),   # 左眼
    (73.5318, 51.5014),   # 右眼
    (56.0252, 71.7366),   # 鼻
]


def _solve_affine_3pt(src_pts, dst_pts):
    """
    用 3 对点解 2x3 仿射矩阵 M = [a,b,c,d,e,f]
      dst_x = a*src_x + b*src_y + c
      dst_y = d*src_x + e*src_y + f
    返回 [a, b, c, d, e, f] 或 None（失败）
    src_pts/dst_pts: 3 个 (x,y) 元组
    """
    try:
        # 系数矩阵 3x3，每行 [sx, sy, 1]，解 [a,b,c] 和 [d,e,f]
        A = np.array([
            [src_pts[0][0], src_pts[0][1], 1.0],
            [src_pts[1][0], src_pts[1][1], 1.0],
            [src_pts[2][0], src_pts[2][1], 1.0],
        ])
        bx = np.array([dst_pts[0][0], dst_pts[1][0], dst_pts[2][0]])
        by = np.array([dst_pts[0][1], dst_pts[1][1], dst_pts[2][1]])
        # 解线性方程 A x = b
        a_b_c = np.linalg.solve(A, bx)
        d_e_f = np.linalg.solve(A, by)
        return [float(a_b_c[0]), float(a_b_c[1]), float(a_b_c[2]),
                float(d_e_f[0]), float(d_e_f[1]), float(d_e_f[2])]
    except Exception as e:
        print(f"[人脸] 仿射求解失败: {e}")
        return None


class FaceDetApp(AIBase):
    """人脸检测应用：face_detection_320.kmodel + aidemo 后处理"""

    def __init__(self, kmodel_path, model_input_size, anchors,
                 conf_th=0.5, nms_th=0.2, rgb888p_size=None, debug_mode=0):
        rgb888p_size = rgb888p_size or [config.RGB888P_WIDTH, config.RGB888P_HEIGHT]
        super().__init__(kmodel_path, model_input_size, rgb888p_size, debug_mode)
        self.confidence_threshold = conf_th
        self.nms_threshold = nms_th
        self.anchors = anchors
        self.model_input_size = model_input_size
        self.rgb888p_size = [_align_up(rgb888p_size[0], 16), rgb888p_size[1]]
        self.ai2d = Ai2d(debug_mode)
        self.ai2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                                 np.uint8, np.uint8)
        self._pad_params = None

    def get_padding_param(self):
        dst_w, dst_h = self.model_input_size
        ratio = min(dst_w / self.rgb888p_size[0], dst_h / self.rgb888p_size[1])
        new_w = int(ratio * self.rgb888p_size[0])
        new_h = int(ratio * self.rgb888p_size[1])
        dw = (dst_w - new_w) / 2
        dh = (dst_h - new_h) / 2
        return (0, int(round(dh * 2 + 0.1)), 0, int(round(dw * 2 - 0.1)))

    def config_preprocess(self, input_image_size=None):
        ai2d_input_size = input_image_size if input_image_size else self.rgb888p_size
        top, bottom, left, right = self.get_padding_param()
        self._pad_params = (top, bottom, left, right)
        self.ai2d.pad([0, 0, 0, 0, top, bottom, left, right], 0, [104, 117, 123])
        self.ai2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
        self.ai2d.build([1, 3, ai2d_input_size[1], ai2d_input_size[0]],
                        [1, 3, self.model_input_size[1], self.model_input_size[0]])

    def postprocess(self, results):
        """aidemo.face_det_post_process 返回人脸列表"""
        try:
            post_ret = aidemo.face_det_post_process(
                self.confidence_threshold, self.nms_threshold,
                self.model_input_size[1], self.anchors,
                self.rgb888p_size, results)
            return post_ret[0] if post_ret else []
        except Exception as e:
            print(f"[人脸] 检测后处理异常: {e}")
            return []


class FaceRecApp(AIBase):
    """人脸特征提取应用：face_recognition.kmodel，输入 112x112 对齐人脸"""

    def __init__(self, kmodel_path, model_input_size, rgb888p_size=None, debug_mode=0):
        rgb888p_size = rgb888p_size or [config.RGB888P_WIDTH, config.RGB888P_HEIGHT]
        super().__init__(kmodel_path, model_input_size, rgb888p_size, debug_mode)
        self.model_input_size = model_input_size
        self.rgb888p_size = [_align_up(rgb888p_size[0], 16), rgb888p_size[1]]
        self.ai2d = Ai2d(debug_mode)
        # 对齐后输入是 RGB888 单张人脸
        self.ai2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                                 np.uint8, np.uint8)
        self._current_box = None   # (x,y,w,h) 当前要处理的人脸框（降级 crop 用）
        self._current_M = None     # 仿射矩阵 [a,b,c,d,e,f]（对齐用）

    def set_current_face(self, box=None, affine_M=None):
        """设置当前要提取特征的人脸（box 或仿射矩阵）"""
        self._current_box = box
        self._current_M = affine_M

    def config_preprocess(self, input_image_size=None):
        """根据当前人脸配置 ai2d：优先仿射对齐，否则 crop+resize
        注意：ai2d.crop/affine 的 build input_shape 必须是【整帧输入】shape，
        不是裁剪后尺寸——否则维度不匹配会导致 KPU 硬崩溃闪退。
        """
        ai2d_input_size = input_image_size if input_image_size else self.rgb888p_size
        in_shape = [1, 3, ai2d_input_size[1], ai2d_input_size[0]]   # 整帧 [1,3,480,640]
        out_size = self.model_input_size
        out_shape = [1, 3, out_size[1], out_size[0]]                # [1,3,112,112]
        if self._current_M is not None:
            # 仿射对齐：整帧 -> 112x112
            self.ai2d.affine(nn.interp_method.bilinear, self._current_M)
            self.ai2d.build(in_shape, out_shape)
        elif self._current_box is not None:
            # 降级：按人脸框 crop + resize（build input 仍是整帧 shape）
            x, y, w, h = self._current_box
            self.ai2d.crop(int(x), int(y), int(w), int(h))
            self.ai2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
            self.ai2d.build(in_shape, out_shape)
        else:
            self.ai2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
            self.ai2d.build(in_shape, out_shape)

    def postprocess(self, results):
        """取模型输出向量并 L2 归一化"""
        try:
            # results 是 list/tuple of tensor，取第一个
            t = results[0] if isinstance(results, (list, tuple)) else results
            # 转为 ulab 数组
            feat = None
            if hasattr(t, 'to_numpy'):
                feat = t.to_numpy()
            else:
                feat = np.array(t)
            # 展平
            feat = feat.reshape(-1)
            # L2 归一化
            norm = float(np.sqrt(np.sum(feat * feat)) + 1e-9)
            feat = feat / norm
            return feat
        except Exception as e:
            print(f"[人脸] 特征后处理异常: {e}")
            return None


class FaceDetector:
    """
    人脸检测+识别+录入总控

    对外接口与参考 face_detector.py 对齐：
      detect_and_recognize(img) -> [(x,y,w,h,points,class_id,score)]
      detect_faces_only(img)    -> [(x,y,w,h,points,class_id,score)]
      start_enrollment(label) / enroll_face(img) -> (success, msg, count)
      get_face_label / is_known_face / get_class_count / get_labels
      clear_all_faces / set_detect_threshold
    """

    def __init__(self, faces_db_path="", conf_th=None, iou_th=None, recognize_th=None,
                 use_alignment=True):
        self._faces_db_dir = faces_db_path if faces_db_path else config.FACES_DB_DIR
        self._detect_conf_th = conf_th if conf_th is not None else config.FACE_CONF_THRESHOLD
        self._detect_iou_th = iou_th if iou_th is not None else config.FACE_IOU_THRESHOLD
        self._recognize_th = recognize_th if recognize_th is not None else config.FACE_RECOGNIZE_THRESHOLD
        self._use_alignment = use_alignment

        # 确保人脸库目录存在
        _ensure_dir(self._faces_db_dir)

        # 加载 anchors
        self._anchors = self._load_anchors(config.FACE_DETECT_ANCHORS)

        # 检测应用
        print("[人脸] 加载人脸检测模型:", config.FACE_DETECT_MODEL)
        self._det_app = FaceDetApp(
            kmodel_path=config.FACE_DETECT_MODEL,
            model_input_size=config.FACE_DETECT_INPUT_SIZE,
            anchors=self._anchors,
            conf_th=self._detect_conf_th,
            nms_th=self._detect_iou_th,
            rgb888p_size=[config.RGB888P_WIDTH, config.RGB888P_HEIGHT],
        )
        self._det_app.config_preprocess()

        # 特征提取应用
        print("[人脸] 加载人脸识别模型:", config.FACE_RECOGNITION_MODEL)
        self._rec_app = FaceRecApp(
            kmodel_path=config.FACE_RECOGNITION_MODEL,
            model_input_size=config.FACE_RECOGNITION_INPUT_SIZE,
            rgb888p_size=[config.RGB888P_WIDTH, config.RGB888P_HEIGHT],
        )

        # 人脸库：label -> 特征向量(ulab ndarray)
        self._face_db = {}     # label -> list of feature vectors（可多人脸特征）
        self._labels = ["unknown"]
        self._load_face_db()

        # 录入状态
        self._is_enrolling = False
        self._enroll_label = ""

        print(f"[人脸] 已录入人脸: {len(self._labels) - 1} 个, 标签: {self._labels[1:]}")

    # ---------- anchors ----------
    def _load_anchors(self, path):
        try:
            if not _path_exists(path):
                print(f"[人脸] anchors 文件不存在: {path}")
                return None
            # prior_data_320.bin 是 float32，shape (4200, 4)
            anchors = np.fromfile(path, dtype=np.float).reshape((4200, 4))
            print(f"[人脸] 加载 anchors: {path}, shape={anchors.shape}")
            return anchors
        except Exception as e:
            print(f"[人脸] 加载 anchors 失败: {e}")
            return None

    # ---------- 人脸库 ----------
    def _label_to_path(self, label):
        safe = label.replace("/", "_").replace(" ", "_")
        return self._faces_db_dir + safe + ".bin"

    def _load_face_db(self):
        try:
            files = os.listdir(self._faces_db_dir)
        except Exception:
            files = []
        for f in files:
            if not f.endswith(".bin"):
                continue
            label = f[:-4]
            try:
                feat = np.fromfile(self._faces_db_dir + f, dtype=np.float)
                if len(feat) > 0:
                    self._face_db[label] = [feat]
                    if label not in self._labels:
                        self._labels.append(label)
            except Exception as e:
                print(f"[人脸] 加载 {label} 失败: {e}")

    def _save_face_feature(self, label, feat):
        """保存特征：先存内存库（保证同会话识别可用），再持久化到 .bin 文件"""
        # 先存内存
        self._face_db[label] = [feat]
        if label not in self._labels:
            self._labels.append(label)
        # 再写文件（ulab ndarray 无 tofile，用 array 模块写 float32 二进制）
        try:
            import array
            arr = array.array('f', feat.tolist())
            with open(self._label_to_path(label), 'wb') as f:
                f.write(arr)
            return True
        except Exception as e:
            print(f"[人脸] 保存文件失败（内存库仍可用）: {e}")
            return True

    # ---------- 检测 ----------
    def _detect_raw(self, img_np):
        """运行检测模型，返回原始后处理结果"""
        try:
            res = self._det_app.run(img_np)
            return res if res else []
        except Exception as e:
            print(f"[人脸] 检测异常: {e}")
            return []

    def _parse_faces(self, det_ret):
        """
        解析检测后处理结果 -> 统一的人脸字典列表
        aidemo.face_det_post_process 返回每人脸一个 ndarray 条目。
        实测格式：[x, y, w, h]（4 值，无 score、无关键点）
        若固件返回带 score/关键点，也兼容。
        """
        faces = []
        if not det_ret:
            return faces
        self._parse_dbg = getattr(self, '_parse_dbg', 0) + 1
        for d in det_ret:
            try:
                vals = list(d)
                # 诊断：打印实际格式
                if self._parse_dbg % 60 == 1:
                    print(f"[人脸] parse dbg: type={type(d).__name__}, len={len(vals)}, vals={list(vals[:16])}")
                if len(vals) < 4:
                    continue
                x, y, w, h = float(vals[0]), float(vals[1]), float(vals[2]), float(vals[3])
                # score：有则取，无则默认 1.0
                score = float(vals[4]) if len(vals) > 4 else 1.0
                kp = []
                # 仅当有关键点数据（>=15 值）才解析
                if len(vals) >= 15:
                    for i in range(5):
                        kp.append((float(vals[5 + i*2]), float(vals[6 + i*2])))
                faces.append({
                    'x': x, 'y': y, 'w': w, 'h': h,
                    'points': kp, 'score': score,
                    'class_id': 0, 'label': 'unknown',
                })
            except Exception as e:
                if self._parse_dbg % 60 == 1:
                    print(f"[人脸] 解析人脸条目异常: {e}, d={d}")
                continue
        return faces

    # ---------- 对齐 + 特征 ----------
    def _compute_affine(self, face):
        """用左眼/右眼/鼻 3 点算仿射矩阵，失败返回 None"""
        kp = face.get('points')
        if not kp or len(kp) < 3:
            return None
        src = [(kp[0][0], kp[0][1]), (kp[1][0], kp[1][1]), (kp[2][0], kp[2][1])]
        return _solve_affine_3pt(src, _ARC_STD_3)

    def _extract_feature(self, img_np, face):
        """对单个人脸对齐+提特征，返回归一化特征向量或 None"""
        try:
            affine_M = None
            box = None
            if self._use_alignment:
                affine_M = self._compute_affine(face)
            if affine_M is None:
                # 降级 crop（外扩 10%，并钳制到图像边界，防止 ai2d.crop 越界崩溃）
                x, y, w, h = face['x'], face['y'], face['w'], face['h']
                ex = w * 0.1
                ey = h * 0.1
                x1 = max(0.0, x - ex)
                y1 = max(0.0, y - ey)
                x2 = min(float(config.RGB888P_WIDTH), x + w + ex)
                y2 = min(float(config.RGB888P_HEIGHT), y + h + ey)
                box = (x1, y1, x2 - x1, y2 - y1)

            self._rec_app.set_current_face(box=box, affine_M=affine_M)
            self._rec_app.config_preprocess()
            feat = self._rec_app.run(img_np)
            return feat
        except Exception as e:
            print(f"[人脸] 特征提取异常: {e}")
            return None

    def _match_feature(self, feat):
        """与库内所有特征匹配，返回 (label, similarity)"""
        if feat is None:
            return ("unknown", 0.0)
        best_label = "unknown"
        best_sim = 0.0
        self._match_cnt = getattr(self, '_match_cnt', 0) + 1
        mtrace = (self._match_cnt <= 8)
        for label, feats in self._face_db.items():
            for db_feat in feats:
                try:
                    # 已归一化，点积即余弦
                    sim = float(np.dot(feat, db_feat))
                    sim = sim / 2.0 + 0.5   # 映射到 [0,1]
                    if mtrace:
                        print(f"[人脸] match {label}: sim={sim:.3f}")
                    if sim > best_sim:
                        best_sim = sim
                        best_label = label
                except Exception as e:
                    if mtrace:
                        print(f"[人脸] match异常 {label}: {e}")
                    continue
        if mtrace:
            print(f"[人脸] match best: {best_label} sim={best_sim:.3f} th={self._recognize_th}")
        if best_sim < self._recognize_th:
            return ("unknown", best_sim)
        return (best_label, best_sim)

    # ---------- 对外接口 ----------
    def detect_and_recognize(self, img_np):
        """检测并识别人脸，img_np 为 RGB888 numpy（整帧）"""
        det_ret = self._detect_raw(img_np)
        faces = self._parse_faces(det_ret)
        # 诊断：每 120 帧打印一次检测结果
        self._dbg_counter = getattr(self, '_dbg_counter', 0) + 1
        if self._dbg_counter % 120 == 1:
            print(f"[人脸] dbg: det_ret={len(det_ret) if det_ret else 0}, parsed={len(faces)}")
        for f in faces:
            feat = self._extract_feature(img_np, f)
            label, sim = self._match_feature(feat)
            f['label'] = label
            f['score'] = sim
            f['class_id'] = self._labels.index(label) if label in self._labels else 0
        gc.collect()
        return faces

    def detect_faces_only(self, img_np):
        """仅检测（不提取特征），用于录入态快速检测"""
        det_ret = self._detect_raw(img_np)
        faces = self._parse_faces(det_ret)
        gc.collect()
        return faces

    def detect(self, img_np):
        return self.detect_and_recognize(img_np)

    # ---------- 录入 ----------
    def start_enrollment(self, label):
        if self._is_enrolling:
            return False
        self._is_enrolling = True
        self._enroll_label = label
        print(f"[人脸] 开始录入: {label}")
        return True

    def enroll_face(self, img_np):
        """录入当前帧第一张人脸，返回 (success, message, count)"""
        if not self._is_enrolling:
            return (False, "未在录入模式", 0)
        try:
            faces = self.detect_faces_only(img_np)
            if not faces:
                return (False, "未检测到人脸", 0)
            face = faces[0]
            feat = self._extract_feature(img_np, face)
            if feat is None:
                return (False, "特征提取失败", 0)
            label = self._enroll_label
            self._save_face_feature(label, feat)
            self._is_enrolling = False
            self._enroll_label = ""
            print(f"[人脸] 录入成功: {label}")
            return (True, f"录入成功: {label}", len(self._labels) - 1)
        except Exception as e:
            print(f"[人脸] 录入失败: {e}")
            return (False, f"录入失败: {e}", 0)

    def cancel_enrollment(self):
        self._is_enrolling = False
        self._enroll_label = ""

    def is_enrolling(self):
        return self._is_enrolling

    # ---------- 查询 ----------
    def get_face_label(self, face):
        return face.get('label', 'unknown')

    def is_known_face(self, face):
        return face.get('class_id', 0) > 0

    def get_class_count(self):
        return max(0, len(self._labels) - 1)

    def get_labels(self):
        return self._labels

    def delete_face(self, label):
        try:
            path = self._label_to_path(label)
            if _path_exists(path):
                os.remove(path)
            if label in self._face_db:
                del self._face_db[label]
            if label in self._labels:
                self._labels.remove(label)
            return True
        except Exception as e:
            print(f"[人脸] 删除失败: {e}")
            return False

    def clear_all_faces(self):
        try:
            for label in list(self._face_db.keys()):
                self.delete_face(label)
            self._labels = ["unknown"]
            print("[人脸] 已清空所有人脸")
            return True
        except Exception as e:
            print(f"[人脸] 清空失败: {e}")
            return False

    def set_detect_threshold(self, conf_th=None, iou_th=None, recognize_th=None):
        if conf_th is not None:
            self._detect_conf_th = conf_th
            self._det_app.confidence_threshold = conf_th
        if iou_th is not None:
            self._detect_iou_th = iou_th
            self._det_app.nms_threshold = iou_th
        if recognize_th is not None:
            self._recognize_th = recognize_th
        print(f"[人脸] 阈值更新: conf={self._detect_conf_th}, iou={self._detect_iou_th}, rec={self._recognize_th}")

    def get_input_width(self):
        return config.FACE_DETECT_INPUT_SIZE[0]

    def get_input_height(self):
        return config.FACE_DETECT_INPUT_SIZE[1]

    def deinit(self):
        try:
            self._det_app.deinit()
            self._rec_app.deinit()
        except Exception:
            pass
        nn.shrink_memory_pool()
        print("[人脸] 人脸检测器已释放")

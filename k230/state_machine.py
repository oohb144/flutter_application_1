"""
亚博智能 K230 人脸识别智能系统 - 状态机模块（CanMV）

由 maix-dostudy 移植。time.ticks_ms() / time.ticks_diff() 在 CanMV 兼容，
几乎原样保留。唯一改动：from maix import time -> import time。
"""

import time

class StateMachine:
    """
    轻量级状态机

    特点：
    - 无外部依赖
    - 内存占用小
    - 支持进入/退出回调
    - 支持状态超时检测
    """

    def __init__(self, initial_state=0):
        # 当前状态
        self._state = initial_state
        # 上一个状态
        self._prev_state = initial_state
        # 状态处理函数字典
        self._handlers = {}
        # 进入状态回调字典
        self._enter_callbacks = {}
        # 退出状态回调字典
        self._exit_callbacks = {}
        # 状态进入时间戳
        self._state_time = time.ticks_ms()
        # 是否正在转换中（防止重入）
        self._transitioning = False

    @property
    def state(self):
        return self._state

    @property
    def prev_state(self):
        return self._prev_state

    @property
    def state_duration(self):
        return time.ticks_diff(time.ticks_ms(), self._state_time)

    def register_handler(self, state, handler):
        self._handlers[state] = handler

    def register_enter_callback(self, state, callback):
        self._enter_callbacks[state] = callback

    def register_exit_callback(self, state, callback):
        self._exit_callbacks[state] = callback

    def transition(self, new_state):
        # 防止重入
        if self._transitioning:
            return False
        # 相同状态不转换
        if new_state == self._state:
            return False

        self._transitioning = True
        try:
            # 执行退出回调
            if self._state in self._exit_callbacks:
                try:
                    self._exit_callbacks[self._state]()
                except Exception as e:
                    print(f"[状态机] 退出回调异常: {e}")

            self._prev_state = self._state
            self._state = new_state
            self._state_time = time.ticks_ms()

            # 执行进入回调
            if new_state in self._enter_callbacks:
                try:
                    self._enter_callbacks[new_state]()
                except Exception as e:
                    print(f"[状态机] 进入回调异常: {e}")

            print(f"[状态机] {self._prev_state} -> {new_state}")
            return True
        finally:
            self._transitioning = False

    def update(self):
        if self._state in self._handlers:
            try:
                self._handlers[self._state]()
            except Exception as e:
                print(f"[状态机] 处理函数异常: {e}")

    def is_state(self, state):
        return self._state == state

    def get_state_duration(self):
        return self.state_duration

    def reset(self):
        self.transition(0)

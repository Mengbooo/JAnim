from typing import Optional
import numpy as np

from PySide6.QtCore import Qt
from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage, QMatrix4x4, QPaintEvent, QKeyEvent
from PySide6.QtWidgets import QWidget
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtOpenGL import *
from OpenGL.GL import *

from janim.constants import *
from janim.scene import Scene
from janim.items.dot_cloud import DotCloud

import time

class GLWidget(QOpenGLWidget):
    frame_rate = DEFAULT_FRAME_RATE

    def __init__(self, parent: Optional[QWidget]=None) -> None:
        super().__init__(parent)

        # 基本属性
        # self.frameRate = 30
        self.setMinimumSize(100, 100)

        # 定时器，用于定时调用绘制，详见 `paintEvent`
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)  # 使定时更准确
        self.timer.setSingleShot(True)                      # 由于每次触发时间不确定，因此是单次触发，每次触发后另行控制
        self.timer.timeout.connect(self.update)             # 达到定时后调用 `update`

        # 场景
        self.scene: Scene = None

        # 仅测试
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.scene = Scene()
        d1 = DotCloud([LEFT * 6 + RIGHT * 0.5 * i for i in range(25)])\
            .set_color([RED, GREEN, BLUE])\
            .set_radii([0.1, 0.05, 0.1, 0.05])
        d2 = DotCloud([LEFT, RIGHT, UP, DOWN])\
            .next_to(d1, DOWN, aligned_edge=RIGHT)\
            .set_radius(0.1)
        self.scene.add(d1, d2)

    def initializeGL(self) -> None:
        glClearColor(0.2, 0.3, 0.3, 1.0)
        # glClearColor(0, 0, 0, 1)    # 将背景色设置为黑色
        glEnable(GL_MULTISAMPLE)    # 抗锯齿

        # 颜色混合
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        # self.tex1 = QOpenGLTexture(QOpenGLTexture.Target.Target2D)
        # self.tex1.create()
        # self.tex1.setData(QImage('assets/container.jpg').mirrored(False, True))
        # self.tex1.setMinMagFilters(QOpenGLTexture.Filter.Linear, QOpenGLTexture.Filter.Linear)
        # self.tex1.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)

        # self.tex2 = QOpenGLTexture(QOpenGLTexture.Target.Target2D)
        # self.tex2.create()
        # self.tex2.setData(QImage('assets/awesomeface.png').mirrored(False, True))
        # self.tex2.setMinMagFilters(QOpenGLTexture.Filter.Linear, QOpenGLTexture.Filter.Linear)
        # self.tex2.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)
        
        # glUniform1i(self.shader.uniformLocation('texture1'), 0)
        # glUniform1i(self.shader.uniformLocation('texture2'), 1)

    def paintGL(self) -> None:
        if self.scene is None:
            return
        
        self.scene.render()

        # glActiveTexture(GL_TEXTURE0)
        # self.tex1.bind()
        # glActiveTexture(GL_TEXTURE1)
        # self.tex2.bind()

        # self.shader.bind()

    def resizeGL(self, w: int, h: int) -> None:
        super().resizeGL(w, h)
        glViewport(0, 0, w, h)
        if self.scene:
            self.scene.camera.window_shape = (w, h)

    def paintEvent(self, e: QPaintEvent) -> None:
        '''
        重载 `paintEvent`，用于计算 `paintGL` 用时，
        并将计划用时（默认 1 / 30 s）减去 `paintGL` 用时后，作为定时器的触发时间，
        这样就可以做到每次间隔计划用时调用绘制

        如果 `paintGL` 用时超过计划用时，则立即调用下一次 update
        
        注：这里的 update 不会对物件数据造成变动，仅用于定时更新画面
        '''
        start = time.perf_counter()
        super().paintEvent(e)
        elapsed = time.perf_counter() - start
        plan = 1 / self.frame_rate
        if elapsed < plan:
            self.timer.start((plan - elapsed) * 1000)
        else:
            self.update()
    
    # 仅测试
    def keyPressEvent(self, event: QKeyEvent) -> None:
        super().keyPressEvent(event)
        if event.key() == Qt.Key.Key_Return:
            self.scene.camera.rotate(2 * DEGREES, UP)

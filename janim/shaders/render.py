from __future__ import annotations
from typing import Optional
import os

from PySide6.QtCore import QObject
from PySide6.QtGui import QMatrix4x4
from PySide6.QtOpenGL import *
from OpenGL.GL import *

from janim.constants import *

class ShaderProgram(QOpenGLShaderProgram):
    keys = (
        ('.vert', QOpenGLShader.ShaderTypeBit.Vertex),
        ('.geom', QOpenGLShader.ShaderTypeBit.Geometry),
        ('.frag', QOpenGLShader.ShaderTypeBit.Fragment)
    )

    filename_to_code_map: dict[str, ShaderProgram] = {}

    @staticmethod
    def get(filename: str) -> ShaderProgram:
        if filename in ShaderProgram.filename_to_code_map:
            return ShaderProgram.filename_to_code_map[filename]
        
        shader = ShaderProgram(filename)
        ShaderProgram.filename_to_code_map[filename] = shader
        return shader
    
    def __init__(self, path_name: str, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        
        for suffix, shader_type in self.keys:
            file_path = path_name + suffix
            if os.path.exists(file_path):
                self.addShaderFromSourceFile(shader_type, file_path)
        
        if not self.link():
            print(f'Failed to link shader "{path_name}"')
            exit(1)

class RenderData:
    def __init__(
        self,
        camera_matrix: QMatrix4x4, 
        wnd_mul_proj_matrix: QMatrix4x4,
        anti_alias_width: float
    ) -> None:
        self.view_matrix = camera_matrix
        self.wnd_mul_proj_matrix = wnd_mul_proj_matrix
        self.anti_alias_width = anti_alias_width

class Renderer:
    def __init__(self) -> None:
        self.initialized = False
        self.needs_update = True

    def prepare(self, item) -> None:
        if not self.initialized:
            self.init()
            self.initialized = True
        
        if self.needs_update:
            self.update(item)
            self.needs_update = False

    def init(self) -> None:
        pass

    def update(self, item) -> None:
        pass

    def pre_render(self, item, data: RenderData) -> None:
        pass
    
    def render(self, item, data: RenderData) -> None:
        pass

    @staticmethod
    def setMat4(shader: ShaderProgram, name: str, mat: QMatrix4x4) -> None:
        shader.setUniformValue(shader.uniformLocation(name), mat)

    @staticmethod
    def setFloat(shader: ShaderProgram, name: str, val: float) -> None:
        shader.setUniformValue1f(shader.uniformLocation(name), val)


class DotCloudRenderer(Renderer):
    def init(self) -> None:
        self.shader = ShaderProgram.get('shaders/dotcloud')
        self.shader.bind()

        self.vao = glGenVertexArrays(1)
        self.vbo_points, self.vbo_rgbas, self.vbo_radii = glGenBuffers(3)
    
    def update(self, item) -> None:
        self.shader.bind()
        glBindVertexArray(self.vao)

        points = item.get_points()
        points_data_size = points.size * FLOAT_SIZE
        rgbas = item.get_rgbas()
        rgbas_data_size = rgbas.size * FLOAT_SIZE
        radii = item.get_radii()
        radii_data_size = radii.size * FLOAT_SIZE

        glBindBuffer(GL_ARRAY_BUFFER, self.vbo_points)
        glBufferData(GL_ARRAY_BUFFER, points_data_size, points, GL_STATIC_DRAW)
        glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 3 * FLOAT_SIZE, ctypes.c_void_p(0))
        glEnableVertexAttribArray(0)
        
        glBindBuffer(GL_ARRAY_BUFFER, self.vbo_rgbas)
        glBufferData(GL_ARRAY_BUFFER, rgbas_data_size, rgbas, GL_STATIC_DRAW)
        glVertexAttribPointer(1, 4, GL_FLOAT, GL_FALSE, 4 * FLOAT_SIZE, ctypes.c_void_p(0))
        glEnableVertexAttribArray(1)

        glBindBuffer(GL_ARRAY_BUFFER, self.vbo_radii)
        glBufferData(GL_ARRAY_BUFFER, radii_data_size, radii, GL_STATIC_DRAW)
        glVertexAttribPointer(2, 1, GL_FLOAT, GL_FALSE, FLOAT_SIZE, ctypes.c_void_p(0))
        glEnableVertexAttribArray(2)

        glBindBuffer(GL_ARRAY_BUFFER, 0)

        glBindVertexArray(0)
    
    def render(self, item, data: RenderData) -> None:
        self.shader.bind()

        self.setMat4(self.shader, 'view_matrix', data.view_matrix)
        self.setMat4(self.shader, 'wnd_mul_proj_matrix', data.wnd_mul_proj_matrix)
        self.setFloat(self.shader, 'anti_alias_width', data.anti_alias_width)

        glBindVertexArray(self.vao)
        glDrawArrays(GL_POINTS, 0, len(item.points))
        glBindVertexArray(0)


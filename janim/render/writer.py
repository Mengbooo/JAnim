import os
import shutil
import subprocess as sp
import time
from functools import partial

import moderngl as mgl
from tqdm import tqdm as ProgressDisplay

from janim.anims.timeline import TimelineAnim
from janim.exception import EXITCODE_FFMPEG_NOT_FOUND, ExitException
from janim.logger import log


class VideoWriter:
    '''
    将时间轴动画生成视频输出到文件中

    可以直接调用 ``VideoWriter.writes(MyTimeline().build())`` 进行输出

    主要流程在 :meth:`write_all` 中：

    - 首先调用 ffmpeg，这里用它生成视频（先输出到 _temp 文件中）
    - 然后遍历动画的每一帧，进行渲染，并将像素数据传递给 ffmpeg
    - 最后结束 ffmpeg 的调用，完成 _temp 文件的输出
    - 将 _temp 文件改名，删去 "_temp" 后缀，完成视频输出
    '''
    def __init__(self, anim: TimelineAnim):
        self.anim = anim
        self.ctx = mgl.create_standalone_context()
        self.ctx.enable(mgl.BLEND)
        self.ctx.blend_func = (
            mgl.SRC_ALPHA, mgl.ONE_MINUS_SRC_ALPHA,
            mgl.ONE, mgl.ONE
        )
        self.ctx.blend_equation = mgl.FUNC_ADD, mgl.MAX

        pw, ph = anim.cfg.pixel_width, anim.cfg.pixel_height
        self.fbo = self.ctx.framebuffer(
            color_attachments=self.ctx.texture(
                (pw, ph),
                components=4,
                samples=0,
            ),
            depth_attachment=self.ctx.depth_renderbuffer(
                (pw, ph),
                samples=0
            )
        )

    def write_all(self, file_path: str, *, quiet=False) -> None:
        '''将时间轴动画输出到文件中

        - 指定 ``quiet=True``，则不会输出前后的提示信息，但仍有进度条
        '''
        name = self.anim.timeline.__class__.__name__
        if not quiet:
            log.info(f'Writing "{name}"')
            t = time.time()

        self.fbo.use()
        fps = self.anim.cfg.fps

        self.open_video_pipe(file_path)

        progress_display = ProgressDisplay(
            range(round(self.anim.global_range.duration * fps) + 1),
            leave=False,
            dynamic_ncols=True
        )

        rgb = self.anim.cfg.background_color.rgb

        for frame in progress_display:
            self.fbo.clear(*rgb)
            self.anim.anim_on(frame / fps)
            self.anim.render_all(self.ctx)
            bytes = self.fbo.read(components=4)
            self.writing_process.stdin.write(bytes)

        self.close_video_pipe()

        if not quiet:
            log.info(f'Finished writing "{name}" in {time.time() - t:.2f} s')
            log.info(f'File saved to "{file_path}"')

    def open_video_pipe(self, file_path: str) -> None:
        stem, ext = os.path.splitext(file_path)
        self.final_file_path = file_path
        self.temp_file_path = stem + '_temp' + ext

        command = [
            self.anim.cfg.ffmpeg_bin,
            '-y',   # overwrite output file if it exists
            '-f', 'rawvideo',
            '-s', f'{self.anim.cfg.pixel_width}x{self.anim.cfg.pixel_height}',  # size of one frame
            '-pix_fmt', 'rgba',
            '-r', str(self.anim.cfg.fps),  # frames per second
            '-i', '-',  # The input comes from a pipe
            '-vf', 'vflip',
            '-an',  # Tells FFMPEG not to expect any audio
            '-loglevel', 'error',
        ]

        if ext == ".mov":
            # This is if the background of the exported
            # video should be transparent.
            command += [
                '-vcodec', 'qtrle',
            ]
        else:
            command += [
                '-vcodec', 'libx264',
                '-pix_fmt', 'yuv420p',
            ]

        command += [self.temp_file_path]
        try:
            self.writing_process = sp.Popen(command, stdin=sp.PIPE)
        except FileNotFoundError:
            log.error('无法输出视频，需要安装 ffmpeg 并将其添加到环境变量中')
            raise ExitException(EXITCODE_FFMPEG_NOT_FOUND)

    def close_video_pipe(self) -> None:
        self.writing_process.stdin.close()
        self.writing_process.wait()
        self.writing_process.terminate()
        shutil.move(self.temp_file_path, self.final_file_path)

    @staticmethod
    def writes(anim: TimelineAnim, file_path: str, *, quiet=False) -> None:
        VideoWriter(anim).write_all(file_path, quiet=quiet)


class AudioWriter:
    def __init__(self, anim: TimelineAnim):
        self.anim = anim

    def write_all(self, file_path: str, *, quiet=False) -> None:
        name = self.anim.timeline.__class__.__name__
        if not quiet:
            log.info(f'Writing audio of "{name}"')
            t = time.time()

        fps = self.anim.cfg.fps
        framerate = self.anim.cfg.audio_framerate

        self.open_audio_pipe(file_path)

        progress_display = ProgressDisplay(
            range(round(self.anim.global_range.duration * fps) + 1),
            leave=False,
            dynamic_ncols=True
        )

        get_audio_samples = partial(self.anim.timeline.get_audio_samples_of_frame,
                                    fps,
                                    framerate)

        for frame in progress_display:
            samples = get_audio_samples(frame)
            self.writing_process.stdin.write(samples.tobytes())

        self.close_audio_pipe()

        if not quiet:
            log.info(f'Finished writing audio of "{name}" in {time.time() - t:.2f} s')
            log.info(f'File saved to "{file_path}"')

    def open_audio_pipe(self, file_path: str) -> None:
        stem, ext = os.path.splitext(file_path)
        self.final_file_path = file_path
        self.temp_file_path = stem + '_temp' + ext

        command = [
            self.anim.cfg.ffmpeg_bin,
            '-y',   # overwrite output file if it exists
            '-f', 's16le',
            '-ar', str(self.anim.cfg.audio_framerate),     # framerate & samplerate
            '-ac', '1',
            '-i', '-',
            '-loglevel', 'error',
            self.temp_file_path
        ]

        try:
            self.writing_process = sp.Popen(command, stdin=sp.PIPE)
        except FileNotFoundError:
            log.error('无法输出音频，需要安装 ffmpeg 并将其添加到环境变量中')
            raise ExitException(EXITCODE_FFMPEG_NOT_FOUND)

    def close_audio_pipe(self) -> None:
        self.writing_process.stdin.close()
        self.writing_process.wait()
        self.writing_process.terminate()
        shutil.move(self.temp_file_path, self.final_file_path)

    @staticmethod
    def writes(anim: TimelineAnim, file_path: str, *, quiet=False) -> None:
        AudioWriter(anim).write_all(file_path, quiet=quiet)

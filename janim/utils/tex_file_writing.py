import os, sys
import hashlib
from contextlib import contextmanager

from janim.config import get_configuration, get_janim_dir
from janim.utils.directories import get_tex_dir
from janim.logger import log

# TODO: [L] perhaps this should live elsewise
@contextmanager
def display_during_execution(message: str):
    # Only show top line
    to_print = message.split("\n")[0]
    max_characters = os.get_terminal_size().columns - 1
    if len(to_print) > max_characters:
        to_print = to_print[:max_characters - 3] + "..."
    try:
        print(to_print, end="\r")
        yield
    finally:
        print(" " * len(to_print), end="\r")


def tex_hash(tex_file_content: str):
    # Truncating at 16 bytes for cleanliness
    hasher = hashlib.sha256(tex_file_content.encode())
    return hasher.hexdigest()[:16]

SAVED_TEX_CONF = {}

def get_tex_conf():
    if not SAVED_TEX_CONF:
        conf = get_configuration()
        SAVED_TEX_CONF.update(conf['tex'][conf['tex']['default']])
        
        template_filepath = os.path.join(
            get_janim_dir(), 'tex_templates',
            SAVED_TEX_CONF['template_file']
        )
        with open(template_filepath, 'r', encoding='utf-8') as f:
            SAVED_TEX_CONF['tex_body'] = f.read()
        
    return SAVED_TEX_CONF


def tex_to_svg_file(tex_file_content):
    svg_file = os.path.join(
        get_tex_dir(), tex_hash(tex_file_content) + ".svg"
    )
    if not os.path.exists(svg_file):
        # If svg doesn't exist, create it
        tex_to_svg(tex_file_content, svg_file)
    return svg_file

def tex_to_svg(tex_file_content: str, svg_file: str):
    tex_file = svg_file.replace(".svg", ".tex")
    with open(tex_file, "w", encoding="utf-8") as outfile:
        outfile.write(tex_file_content)
    svg_file = dvi_to_svg(tex_to_dvi(tex_file))

    # Cleanup superfluous documents
    tex_dir, name = os.path.split(svg_file)
    stem, end = name.split(".")
    for file in filter(lambda s: s.startswith(stem), os.listdir(tex_dir)):
        if not file.endswith(end):
            os.remove(os.path.join(tex_dir, file))

    return svg_file

def tex_to_dvi(tex_file: str) -> str:
    conf = get_tex_conf()

    program = conf['executable']
    file_type = conf['intermediate_filetype']

    result = tex_file.replace(".tex", "." + file_type)
    if not os.path.exists(result):
        commands = [
            program,
            "-interaction=batchmode",
            "-halt-on-error",
            f"-output-directory=\"{os.path.dirname(tex_file)}\"",
            f"\"{tex_file}\"",
            ">",
            os.devnull
        ]
        exit_code = os.system(" ".join(commands))
        if exit_code != 0:
            log_file = tex_file.replace(".tex", ".log")
            log.error("LaTeX Error!  Not a worry, it happens to the best of us.")
            with open(log_file, "r", encoding="utf-8") as file:
                flag = False
                err = ''
                for line in file.readlines():
                    if flag and line.isspace():
                        break
                    if line.startswith("!"):
                        flag = True
                    
                    if flag:
                        err += line
                
                log.debug(f"The error could be: \n{err[:-1]}")

            sys.exit(2)
    return result


def dvi_to_svg(dvi_file: str, regen_if_exists=False) -> str:
    """
    Converts a dvi, which potentially has multiple slides, into a
    directory full of enumerated pngs corresponding with these slides.
    Returns a list of PIL Image objects for these images sorted as they
    where in the dvi
    """
    conf = get_tex_conf()

    file_type = conf['intermediate_filetype']

    result = dvi_file.replace("." + file_type, ".svg")
    if not os.path.exists(result):
        commands = [
            "dvisvgm",
            "\"{}\"".format(dvi_file),
            "-n",
            "-v",
            "0",
            "-o",
            "\"{}\"".format(result),
            ">",
            os.devnull
        ]
        os.system(" ".join(commands))
    return result
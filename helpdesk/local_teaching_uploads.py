"""Select the user's original local skill folder; never rewrite teaching text."""
import os
from pathlib import Path

SKILLS_ROOT=Path.home()/'.claude/skills'
QUESTION_FOLDERS={
    '阅读理解':'gaokao-english','七选五':'gaokao-english','完形填空':'gaokao-english',
    '听力':'gaokao-english','语法填空':'gaokao-grammar-fill',
    '应用文':'gaokao-writing','读后续写':'gaokao-writing',
}


def local_teaching_files(question_type):
    """Expand exactly one approved folder into unchanged file attachments.

    The selected root may itself be the user's deployment junction. Descendant
    redirects cannot escape that resolved root. Existing browser receipts hash
    every attachment and stop changed requests; no extra teaching manifest or
    JSON is generated/uploaded here.
    """
    if question_type not in QUESTION_FOLDERS:
        raise ValueError('LOCAL_TEACHING_QUESTION_TYPE_REQUIRED')
    root=(SKILLS_ROOT/QUESTION_FOLDERS[question_type]).resolve(strict=True)
    if not root.is_dir() or not (root/'SKILL.md').is_file():
        raise ValueError('LOCAL_TEACHING_SKILL_MISSING')
    result=[]
    for directory,subdirs,names in os.walk(root,followlinks=False):
        subdirs[:]=sorted(name for name in subdirs if not name.startswith('.') and name!='__pycache__')
        for name in [*subdirs,*names]:
            path=Path(directory)/name
            if not path.resolve(strict=True).is_relative_to(root):
                raise ValueError('LOCAL_TEACHING_PATH_OUTSIDE_SELECTED_FOLDER')
        for name in sorted(names):
            path=Path(directory)/name
            if not name.startswith('.') and path.suffix.lower() in ('.md','.txt','.py') and path.is_file():
                result.append(path)
    if not result:
        raise ValueError('LOCAL_TEACHING_FILES_MISSING')
    return sorted(result,key=lambda path:(path.name!='SKILL.md',path.relative_to(root).as_posix()))

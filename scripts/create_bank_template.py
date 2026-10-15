"""Create an empty workbook matching the deployed question-bank schema."""

import os
from pathlib import Path

from openpyxl import Workbook


def create_template(path: Path) -> None:
    if path.exists():
        raise FileExistsError("Refusing to overwrite an existing question bank")
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    single = workbook.active
    single.title = "单选题样题"
    single.append(["序号", "分类", "题目", "A", "B", "C", "D", "答案"])
    boolean = workbook.create_sheet("判断题样题")
    boolean.append(["序号", "分类", "题目", "答案"])
    workbook.save(path)
    workbook.close()


if __name__ == "__main__":
    default = Path(__file__).resolve().parents[1] / "data" / "question_bank.xlsx"
    destination = Path(os.environ.get("QUESTION_BANK_PATH", str(default)))
    create_template(destination)
    print(f"Created empty template: {destination}")

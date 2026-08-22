#!/usr/bin/env python3
"""
一键清理 LIS_v2 调试文件（健壮版）：
1. 删除根目录 debug_logs/ 下所有内容
2. 删除所有 bot 的 tasks/ 目录下所有内容

- 遇无法删除的文件直接跳过并提示，继续下一个
- 统计每个 bot 各清除了多少文件，最后显示总数
"""

import os
import shutil

ROOT = os.path.dirname(os.path.abspath(__file__))
DEBUG_DIR = os.path.join(ROOT, 'debug_logs')
BOTS_DIR = os.path.join(ROOT, 'bots')


def _count_items(path):
    """统计目录下所有文件（包括子目录中的）和目录个数"""
    files = 0
    dirs = 0
    for root, dirnames, filenames in os.walk(path):
        files += len(filenames)
        dirs += len(dirnames)
    return files, dirs


def _remove_item(item):
    """尝试删除一个文件或目录，失败返回错误信息"""
    try:
        if os.path.islink(item) or os.path.isfile(item):
            os.unlink(item)
            return None
        elif os.path.isdir(item):
            shutil.rmtree(item)
            return None
        return f"未知类型: {item}"
    except Exception as e:
        return f"{e}"


def clean_dir(path):
    """删除目录下所有内容，返回 (文件数, 目录数, 失败列表)"""
    files, dirs = _count_items(path) if os.path.isdir(path) else (0, 0)
    errors = []
    if not os.path.isdir(path):
        return 0, 0, errors
    for name in os.listdir(path):
        item = os.path.join(path, name)
        err = _remove_item(item)
        if err:
            errors.append(f"{item}: {err}")
    # 如果目录本身可能还有一些剩余（因为跳过导致），尝试再次删除空目录
    try:
        if os.path.isdir(path) and not os.listdir(path):
            os.rmdir(path)
    except Exception:
        pass
    return files, dirs, errors


def main():
    total_files = 0
    total_dirs = 0
    total_errors = 0
    results = []

    # 清理 debug_logs
    files, dirs, errors = clean_dir(DEBUG_DIR)
    results.append(("debug_logs", files, dirs, errors))

    # 清理所有 bot 的 tasks 目录
    if os.path.isdir(BOTS_DIR):
        for bot_name in sorted(os.listdir(BOTS_DIR)):
            bot_path = os.path.join(BOTS_DIR, bot_name)
            if not os.path.isdir(bot_path):
                continue
            tasks_dir = os.path.join(bot_path, 'tasks')
            files, dirs, errors = clean_dir(tasks_dir)
            if files or dirs or errors:
                results.append((f"{bot_name}/tasks", files, dirs, errors))

    # 输出结果
    print("\n===== 清理完成 =====")
    for label, files, dirs, errors in results:
        print(f"\n[{label}] 清理 {files} 个文件, {dirs} 个目录", end='')
        if errors:
            print(f", {len(errors)} 个失败:")
            for e in errors:
                print(f"  - 跳过: {e}")
        else:
            print()
        total_files += files
        total_dirs += dirs
        total_errors += len(errors)

    print(f"\n总计清理 {total_files} 个文件, {total_dirs} 个目录, 失败 {total_errors} 个")
    if total_errors:
        print("提示：失败文件可能被占用，可稍后手动处理。")


if __name__ == '__main__':
    main()

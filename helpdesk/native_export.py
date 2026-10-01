"""Read-only export of native acquisitions; an acquisition is never a question."""
from datetime import date
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

from .native_intake import MISSING_METADATA, verify_native_record


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def _zip_bytes(files):
    stream = BytesIO()
    with ZipFile(stream, 'w') as archive:
        for name, payload in sorted(files.items()):
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, payload)
    return stream.getvalue()


def export_native_records(root, output, *, scope_start_date='2026-09-17'):
    """Export English-labelled acquisitions to an explicit new directory.

    All candidate archives are verified before writing. Existing directories are
    accepted only when every output byte matches; other contents are never
    overwritten. Repeated acquisitions remain separate artifacts. Scope dates
    are requested dates, not filters on unknown original message timestamps.
    """
    if date.fromisoformat(scope_start_date).isoformat() != scope_start_date:
        raise ValueError('Scope date must use YYYY-MM-DD')
    root = Path(root).resolve(strict=True)
    output = Path(output).resolve()
    if not root.is_dir():
        raise ValueError('Archive root must be a directory')
    if output == root or root in output.parents or output in root.parents:
        raise ValueError('Export directory must be separate from the archive root')
    records, excluded, files, sections = [], [], {}, []
    folders = sorted(root.iterdir(), key=lambda p: (p.name.casefold(), p.name))
    for folder in folders:
        if not folder.is_dir():
            continue
        # Fail closed for collection directories, including missing manifests.
        row = verify_native_record(folder)
        if 'english' not in row['observed_group_label'].casefold():
            excluded.append(folder.name)
            continue
        evidence = json.loads(row['evidence_json'])
        payloads = {
            '原始文字记录.txt': Path(evidence['raw_text_path']).read_bytes(),
            'manifest.json': Path(evidence['manifest_path']).read_bytes(),
            'clipboard-result.json': (folder / 'clipboard-result.json').read_bytes(),
            'clipboard-attempt.json': (folder / 'clipboard-attempt.json').read_bytes(),
            '原始采集凭据/result.json': Path(evidence['original_result_path']).read_bytes(),
            '原始采集凭据/attempt.json': Path(evidence['original_attempt_path']).read_bytes(),
        }
        expected = {'原始文字记录.txt': row['raw_text_sha256'],
                    'manifest.json': row['manifest_sha256'],
                    'clipboard-result.json': row['result_sha256'],
                    'clipboard-attempt.json': row['attempt_sha256'],
                    '原始采集凭据/result.json': row['result_sha256'],
                    '原始采集凭据/attempt.json': row['attempt_sha256']}
        if any(sha256(payloads[name]).hexdigest() != digest for name, digest in expected.items()):
            raise ValueError('Acquisition changed during export')
        prefix = f'采集/{len(records) + 1:04d}-{folder.name}/'
        files.update({prefix + name: payload for name, payload in payloads.items()})
        record = {
            'collection_id': folder.name,
            'acquisition_id': row['acquisition_id'],
            'observed_group_label': row['observed_group_label'],
            'source_directory': row['source_path'],
            'source_kind': 'WINDOWS_MCP_NATIVE_CLIPBOARD',
            'acquisition_started_at': row['acquired_at'],
            'original_sender': None, 'original_message_time': None,
            'verified_group_identity': None, 'membership': None,
            'question_count': None, 'coverage': 'partial',
            'formal_statistics_eligible': False,
            'missing_metadata': list(MISSING_METADATA),
            'evidence_paths': evidence,
            'package_files': {prefix + name: {'sha256': expected[name], 'bytes': len(payload)}
                              for name, payload in payloads.items()},
        }
        records.append(record)
        sections.append(
            f'\n===== 采集件 {len(records):04d}（以下标签为导出说明，不属于原文）=====\n'
            f'观察群名：{row["observed_group_label"]}\n'
            f'采集编号：{row["acquisition_id"]}\n'
            f'采集时间：{row["acquired_at"]}（不是原消息时间）\n'
            '原发送人、原消息时间及群身份：尚未核验\n'
            '----- 原文开始（单件独立原文见 ZIP，字节保持不变）-----\n'
            + row['original_text'] + '\n----- 原文结束 -----\n')
    manifest = {
        'format_version': 1,
        'scope': {'group_label_contains': 'English', 'case_sensitive': False,
                  'include_exited_groups': True, 'requested_start_date': scope_start_date,
                  'original_message_dates_verified': False, 'date_filter_applied': False},
        'coverage': 'partial', 'coverage_complete': False,
        'all_groups_discovered': False, 'all_history_exported': False,
        'formal_statistics_eligible': False,
        'collection_count': len(records), 'question_count': None,
        'excluded_non_english_collections': excluded, 'records': records,
        'limitations': ['采集件数不是消息数、题数或绩效数。',
                        'English 匹配观察群名，不能证明全部群（含退出群）已发现。',
                        '请求起始日期不代表已覆盖该日期以来历史，原消息日期尚未核验。',
                        '重复采集全部保留，尚未去重或确认题目及附件。'],
    }
    files['导出清单.json'] = _json_bytes(manifest)
    files['原始记录汇总.txt'] = (
        f'企业微信 English 群原始文字采集导出\n请求起始日期：{scope_start_date}\n'
        '历史覆盖状态：partial（部分，完整性未核验）\n'
        f'采集件数：{len(records)}；题数：未知；正式统计资格：否\n'
        '包含已采集的退出群；未确认已发现所有群。\n'
        '原始发送人及原消息时间未知；不得以采集时间替代。\n'
        '原文中的图片/附件标记不代表实际附件已导出。\n'
        + ''.join(sections)).encode('utf-8')
    expected_outputs = {name: files[name] for name in ('导出清单.json', '原始记录汇总.txt')}
    expected_outputs['原文与采集凭据.zip'] = _zip_bytes(files)
    reused = False
    if output.exists():
        if (not output.is_dir() or
                {p.name for p in output.iterdir()} != set(expected_outputs) or
                any(not (output / name).is_file() or (output / name).read_bytes() != payload
                    for name, payload in expected_outputs.items())):
            raise FileExistsError('Export directory has different contents; choose a new output directory')
        reused = True
    else:
        output.mkdir(parents=True, exist_ok=False)
        for name, payload in expected_outputs.items():
            with (output / name).open('xb') as handle:
                handle.write(payload)
    return {'output_directory': str(output), 'collection_count': len(records),
            'question_count': None, 'coverage': 'partial',
            'formal_statistics_eligible': False, 'reused_identical_export': reused,
            'files': {name: {'path': str(output / name), 'sha256': sha256(payload).hexdigest()}
                      for name, payload in expected_outputs.items()}}

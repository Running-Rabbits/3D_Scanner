# 仓库定时备份节点（README_BACKUP）

一键式、可独立运行、可断点续传的仓库备份设施。核心是单个幂等脚本
`scripts/backup_repo.ps1`，由 Windows 计划任务每日自动触发，无需打开任何编辑器。

## 功能总览

流水线：`lock → pack → encrypt → verify → upload → rotate → done`

| 阶段 | 说明 |
|---|---|
| lock | 文件互斥锁防并发；残留锁超过 60 分钟自动接管 |
| pack | `git bundle create --all`（全部分支+完整历史）+ 未提交变更快照 zip → 外层 payload.zip |
| encrypt | AES-256-CBC；16 字节随机 IV 前置于密文头部；32 字节密钥存 `scripts/backup_key.bin`（已 gitignore，首次运行自动生成） |
| verify | 整包解密回读，SHA256 与加密前明文比对，不一致即中止 |
| upload | 目标 A：加密产物 force-push 到 origin 的孤儿分支 `backups`；目标 B：拷贝到本地二级目录 |
| rotate | 本地归档与本地目标目录各保留最近 7 份 |
| done | 状态文件写入 `last_success_utc`，下次运行进入 20 小时防重复窗口 |

## 触发器（双保险）

1. **Windows 计划任务**（主）：任务名 `3DScanner_RepoBackup`
   - 每日 03:00 触发；`-StartWhenAvailable`（错过时刻/关机，开机后自动补跑）
   - 执行上限 2 小时；失败自动重试 3 次、间隔 10 分钟
   - 查看/手动触发：`任务计划程序` 或 `Get-ScheduledTask 3DScanner_RepoBackup`
2. **Trae 自动化**（辅）：每日 09:00 健康检查——近 24h 无成功记录则自动补跑一次脚本

脚本幂等：即使被重复触发，20 小时内已有成功备份会直接跳过（日志记 `skip/fresh`）。

## 断点续传与持久化

- 每完成一个阶段，检查点写入 `backups/state/backup_state.json`
- 中断后（对话关闭、断电、进程被杀）下次运行检测到失败状态且距开始 < 6 小时，
  自动复用 `backups/tmp/payload.enc` 等中间产物，从断点阶段继续，已上传过的目标不会重复打包
- 执行日志：`backups/logs/backup_log.jsonl`，每个事件一行 JSON
  （ts / run_id / stage / status / detail / bytes / duration_ms）

## 配置参数（脚本内常量，按需修改）

| 参数 | 默认值 | 含义 |
|---|---|---|
| `$MinIntervalHours` | 20 | 距上次成功不足该小时数则跳过（手动 `-ForceRun` 可越过） |
| `$KeepLocalCopies` | 7 | 本地归档/目标目录保留份数 |
| `$ArtifactTtlHours` | 6 | 中断产物续用窗口（超时则重新打包） |
| `$LocalTargetDir` | `C:\Users\<用户>\.local\share\3D_Scanner_backups` | 本地二级备份目录 |
| `$GitSshExe` | `C:/Windows/System32/OpenSSH/ssh.exe` | 推送所用 SSH（读用户 ssh.config，走 443 端口） |
| 计划任务时间 | 每日 03:00 | 在 `任务计划程序` 中修改触发器即可 |

## 手动运行

```powershell
# 完整备份（20 小时内已成功过则跳过）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\backup_repo.ps1

# 强制完整备份（越过最小间隔）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\backup_repo.ps1 -ForceRun

# 仅打包+加密+校验，不上传
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\backup_repo.ps1 -SkipUpload
```

退出码：0 = 成功或跳过，1 = 失败（原因写入日志与状态文件）。

## 验证备份正常运行的测试步骤

1. **单次全流程**：运行上面第一条命令，末尾输出 `SUCCESS: backup ...`；
   `Get-Content backups\logs\backup_log.jsonl -Tail 10` 应看到
   `pack/encrypt/verify/upload git_branch_ok/upload local_dir_ok/rotate/run success`。
2. **远程分支**：`git ls-remote origin backups` 应返回分支哈希。
3. **本地目标**：`C:\Users\<用户>\.local\share\3D_Scanner_backups` 出现成对的
   `3D_Scanner_backup_<时间戳>.enc` 与 `.meta.json`。
4. **防重复**：不带参数再跑一次，应输出 `SKIP: last successful backup was ... hours ago`。
5. **计划任务拉起**：`Start-ScheduledTask 3DScanner_RepoBackup`，随后日志尾部新增
   `skip/fresh` 记录且 `LastTaskResult` 为 0，说明任务调度链路正常。
6. **断点续传**：运行 `-ForceRun` 过程中直接关掉终端窗口中断它，再运行一次普通命令；
   日志应出现 `resume/detected` 与 `encrypt/resumed`，并最终 `run/success`。

## 恢复（还原）方法

1. 取出密文：`git clone` 本仓库后 `git checkout backups`，取 `payload.enc`；
   或直接取本地二级目录中的 `.enc` 文件。
2. 解密（需 `scripts/backup_key.bin`，或指纹文件记录的密钥原件）：

```powershell
$key = [IO.File]::ReadAllBytes('scripts\backup_key.bin')
$raw = [IO.File]::ReadAllBytes('payload.enc')
$aes = [System.Security.Cryptography.Aes]::Create()
$aes.KeySize = 256; $aes.Mode = 'CBC'; $aes.Padding = 'PKCS7'
$aes.Key = $key; $aes.IV = $raw[0..15]
$ms = New-Object System.IO.MemoryStream -ArgumentList @($raw, 16, ($raw.Length - 16))
$cs = New-Object System.Security.Cryptography.CryptoStream($ms, $aes.CreateDecryptor(), 'Read')
[IO.File]::WriteAllBytes('payload.zip', (& { $out=[IO.MemoryStream]::new(); $cs.CopyTo($out); $out.ToArray() }))
Expand-Archive payload.zip restored_backup
git clone restored_backup\repo.bundle restored_repo   # 完整历史
# restored_backup\workdir_dirty.zip 为打包时未提交的变更，按需解压覆盖
```

## 稳定性注意事项

- **密钥即生命**：`backup_key.bin` 丢失则所有历史备份无法解密。请将
  `scripts/backup_key.fingerprint.txt` 中的密钥另行抄送到仓库之外（密码管理器/U 盘）。
- `backups/` 与密钥文件均已列入 `.gitignore`，不会随主分支提交。
- 推送复用仓库现有 SSH 配置；`backups` 分支永远只有加密产物，可公开仓库下也安全。
- 任意阶段失败都会写入状态与日志并退出码 1，计划任务按 3 次/10 分钟重试。

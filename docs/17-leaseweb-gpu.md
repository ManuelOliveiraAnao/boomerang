# 17 · AI on a Leaseweb server (owner: C)

Boomerang's AI (Ollama) can run on a **Leaseweb GPU server** instead of the laptop. Nothing else moves: the
engine, Docker stack, moto, UI and redaction all stay on the laptop. Only the model's answers come from the server.

```
laptop                                                     Leaseweb GPU server (Ubuntu)
boomerang ── http://127.0.0.1:11435 ══ SSH tunnel (port 22) ══▶ 127.0.0.1:11434  Ollama + GPU
   │  redact() runs here: secrets never leave the laptop       (port 11434 never open to the internet)
```

**Why:** a bigger model fails validation less, and the server is much faster than a laptop. The pitch also gets
stronger: *the AI runs on the same EU provider you're moving to, not on AWS or a US AI API.*

## Our server (set up 2026-11-20)

The Leaseweb instance we got has **no GPU**: 32 vCPU AMD EPYC Genoa (AVX-512), 125 GB RAM, Ubuntu 26.04.
Ollama runs on CPU, which is still far faster than our laptops (i7-10750H, Intel graphics only). Steps 5–8 below
are **done** on it: `ufw` allows SSH only, Ollama 0.40 listens on `127.0.0.1:11434` only, and 7b/14b/32b are pulled.

Measured through the tunnel with a de-lock prompt (`get_order`, 1336 prompt tokens, `num_ctx` 8192):

| Model | Load | Answer | Prompt speed | Output speed |
|---|---|---|---|---|
| `qwen2.5-coder:7b` | 5 s | 12 s | 318 tok/s | 32 tok/s |
| **`qwen2.5-coder:14b`** | 8 s | **26 s** | 160 tok/s | 16 tok/s |
| `qwen2.5-coder:32b` | 18 s | 47 s | 74 tok/s | 9 tok/s |

**We use `qwen2.5-coder:14b`:** better than 7b and well under the 60 s target, with room for a validation retry.
32b is under 60 s for one call but a retry pushes it past, so keep it only for a quality comparison.
The server answers one request at a time: teammates sharing it queue behind each other.

**What changes in the pitch:** don't say "the model runs on this laptop" any more. Say "the model runs on
sovereign EU infrastructure, on Leaseweb". The UI badge says so (`BOOMERANG_LLM_LOCATION=leaseweb`).

> **Never commit the server's IP, user name or password.** They go only in `~/.ssh/config` and your own `.env`,
> both outside git. Share them with teammates privately.

---

## Part A · Get in safely (each teammate who needs access, once)

### 1. Create an SSH key on your laptop

```powershell
ssh-keygen -t ed25519
```
Press Enter to accept the default path. A passphrase is optional.

### 2. First login with the password, then change it

The password from the email counts as exposed: it went by email and screenshot. **One person** does this:

```powershell
ssh ubuntu@<server-ip>
```
```bash
passwd          # pick a new password; share it privately with the team if needed
```

### 3. Add an SSH alias and your key

Add these lines to `C:\Users\<you>\.ssh\config` (create the file if it's missing):

```
Host boomerang-gpu
    HostName <server-ip>
    User ubuntu
    IdentityFile ~/.ssh/id_ed25519
    ServerAliveInterval 15
```

Then copy your public key (this asks for the password one last time):

```powershell
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh boomerang-gpu "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

Check: `ssh boomerang-gpu` must now log in **without** asking for a password.

### 4. Turn off password logins (once every teammate's key works)

On the server:
```bash
echo "PasswordAuthentication no" | sudo tee /etc/ssh/sshd_config.d/00-boomerang.conf
sudo systemctl restart ssh
```
Before you close your session, check from a **second** terminal that `ssh boomerang-gpu` still works.

---

## Part B · Set up the server (one person, once)

Run all of these on the server (`ssh boomerang-gpu`).

### 5. Update and firewall

```bash
sudo apt update && sudo apt -y upgrade
sudo ufw allow OpenSSH
sudo ufw enable
sudo ufw status            # only 22/OpenSSH allowed
```

### 6. Check the GPU

```bash
nvidia-smi
```
- Shows a GPU and its memory → go to step 7.
- `command not found` → install the driver, reboot, check again:
  ```bash
  sudo apt -y install ubuntu-drivers-common
  sudo ubuntu-drivers install
  sudo reboot
  ```
- Still no GPU → the server has none. Stop and stay on the laptop setup (docs/11 Q1).

### 7. Install Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh
systemctl status ollama --no-pager           # active (running)
ss -ltnp | grep 11434                         # must show 127.0.0.1:11434, NOT 0.0.0.0 or *
```

**Never set `OLLAMA_HOST=0.0.0.0` on the server.** Ollama has no password: anyone on the internet could use the
GPU and read your prompts. The tunnel is the only way in.

Keep the model loaded between calls. Boomerang already sends `keep_alive=60m`; this covers manual tests too:
```bash
sudo mkdir -p /etc/systemd/system/ollama.service.d
printf '[Service]\nEnvironment="OLLAMA_KEEP_ALIVE=60m"\n' | sudo tee /etc/systemd/system/ollama.service.d/override.conf
sudo systemctl daemon-reload && sudo systemctl restart ollama
```

### 8. Pull the model that fits the GPU

| GPU memory (`nvidia-smi`) | Model | Note |
|---|---|---|
| 8–16 GB | `qwen2.5-coder:14b` | still better than the laptop's `:7b` |
| **24 GB or more** | **`qwen2.5-coder:32b`** | recommended |

```bash
ollama pull qwen2.5-coder:32b
ollama run qwen2.5-coder:32b "Say OK" && ollama ps     # PROCESSOR column must say 100% GPU
```
If `ollama ps` shows part CPU, the model is too big for the GPU: use the smaller one.

Also pull `qwen2.5-coder:7b`. It lets you compare quality on the same prompts and is a quick fallback.

---

## Part C · Connect Boomerang (every laptop)

### 9. Open the tunnel

In its own PowerShell window, left open the whole time:

```powershell
.\scripts\gpu-tunnel.ps1
```
It forwards laptop `127.0.0.1:11435` → server `127.0.0.1:11434` and reconnects by itself if the Wi-Fi drops.
Port 11435 is used so it doesn't clash with Ollama on the laptop, if that's also running.

Check it:
```powershell
Invoke-RestMethod http://127.0.0.1:11435/api/tags | Select-Object -ExpandProperty models | Select-Object name
```

### 10. Point `.env` at it

```
BOOMERANG_LLM_PROVIDER=ollama
BOOMERANG_MODEL=qwen2.5-coder:14b
OLLAMA_HOST=http://127.0.0.1:11435
BOOMERANG_LLM_LOCATION=leaseweb
```

`BOOMERANG_LLM_LOCATION=leaseweb` makes the UI and report badge read
`ollama · qwen2.5-coder:14b · Leaseweb (EU)` (S5: honest about where the model runs). If the tunnel is down,
the error tells you to open it.

To go back to the laptop: `OLLAMA_HOST=http://127.0.0.1:11434`, `BOOMERANG_LLM_LOCATION=local`,
`BOOMERANG_MODEL=qwen2.5-coder:7b`.

### 11. Benchmark (docs/11 Q1)

```powershell
$env:BOOMERANG_LLM_MODE = "live"
Measure-Command { boomerang migrate sample-app/aws --yes } | Select-Object TotalSeconds
```
Target: < 60 s per de-locked function, and no "failed validation twice" events. Compare with the laptop's `:7b`
on the same app. Keep the 32b only if it's at least as good.

---

## Part D · Lock it in for the demo

### 12. Record the golden run with the GPU model

Follow `demo/llm-cache/README.md` steps 1–4 with the tunnel open and `.env` from step 10. The cache key
includes provider and model, so **the whole team must replay with the same `BOOMERANG_MODEL`** (e.g.
`qwen2.5-coder:32b`), even on laptops without the tunnel. Replay doesn't contact Ollama, so it works offline.

### 13. Demo day

| Situation | What you do |
|---|---|
| Normal | Replay from `demo/llm-cache` (instant, identical). Badge shows Leaseweb GPU because `.env` says so. |
| Judges want a live AI call | Tunnel open, `BOOMERANG_LLM_MODE=live`, de-lock one function live. |
| Venue Wi-Fi blocks port 22 | Replay still works. For live: phone hotspot, or the laptop's `:7b`. |
| Server unreachable | Replay. Live fallback: laptop `:7b` (step 10 "go back") or `BOOMERANG_LLM_PROVIDER=anthropic`. |

---

## Checklist

- [ ] Password changed, keys for everyone, password login off (steps 2–4)
- [ ] `ufw` only allows SSH, Ollama on `127.0.0.1:11434` only (steps 5, 7)
- [ ] `ollama ps` shows 100% GPU for the chosen model (step 8)
- [ ] Tunnel works on every laptop (step 9)
- [ ] Benchmark recorded: time per function, laptop vs GPU (step 11)
- [ ] Golden run recorded and frozen with the GPU model (step 12)
- [ ] Pitch line and docs/13 §2 say "Leaseweb", not "this laptop"
- [ ] No IP, password or `~/.ssh/config` in git (`git diff --staged`)

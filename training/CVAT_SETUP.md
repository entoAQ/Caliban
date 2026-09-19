# CVAT setup (home rig, Windows)

Runbook for getting CVAT running on the home GPU rig, for tagging tray
photos (larva / frass / background) toward a future segmentation model. See
`training/augment_rotations.py` in this same directory for the
rotation/mirror augmentation script that multiplies whatever gets tagged
here 8x for free once there's real output to run it against.

Why Docker + WSL2, and why this is more involved than most setups here:
CVAT's only supported install path is `docker compose`, and Docker Desktop
on Windows requires WSL2 as its backend. There is no simpler native-Windows
path.

## Prerequisites

- Windows, administrator access for the WSL2 install step.
- An NVIDIA GPU, if you want SAM-assisted labeling and later model training
  to actually use it (both work on CPU too, just much slower).

## 1. Install WSL2

In an **administrator** PowerShell or Command Prompt:

```powershell
wsl --install
```

This installs WSL2 and Ubuntu by default. It will likely ask for a restart
to finish — let it.

## 2. GPU driver (host side, not inside WSL)

Install the latest NVIDIA driver from nvidia.com **on Windows itself** —
WSL2 GPU passthrough uses the Windows host driver directly, there is no
separate Linux driver to install inside WSL.

Verify from inside WSL's Ubuntu shell:

```bash
nvidia-smi
```

It should print your GPU. If it doesn't, the driver step above didn't take
— fix that before going further, since Docker's GPU passthrough depends on
it.

## 3. Install Docker Desktop

Install from https://www.docker.com/products/docker-desktop/. After
install, confirm in Docker Desktop's settings:

- **Settings → General**: "Use the WSL 2 based engine" is checked.
- **Settings → Resources → WSL Integration**: your Ubuntu distro is
  enabled.

## 4. Verify GPU passthrough works in Docker

From WSL or PowerShell:

```bash
docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi
```

Should print the same GPU info as step 2's `nvidia-smi`. If this fails but
step 2 worked, the issue is Docker's GPU passthrough config, not the driver.

## 5. Get CVAT running

Inside **WSL's Ubuntu shell**, not Windows' filesystem — Docker's WSL2
backend performs noticeably better with files living on the Linux side
(`~/`) rather than under `/mnt/c/...`:

```bash
git clone https://github.com/cvat-ai/cvat
cd cvat
docker compose up -d
```

## 6. Open it

http://localhost:8080

## 7. Create a login

```bash
docker exec -it cvat_server bash -ic 'python3 ~/manage.py createsuperuser'
```

## 8. (Next step, not yet covered here) SAM-assisted labeling

CVAT's Segment-Anything click-to-mask labeling runs through its
serverless/Nuclio functions, which is a separate `docker compose` overlay
plus deploying the SAM function specifically. Do this once steps 1-7 are
confirmed working — get the base install solid first.

## Remote access: Tailscale, installed vs. web-only

Tailscale itself **must be installed on the home rig** (the host) — there's
no way around that; it's what makes the rig reachable at all. But whether
*you*, accessing CVAT's web UI from elsewhere, need to install anything
depends on which mode is used:

- **Normal tailnet (private mesh)**: every device that wants in — the rig
  *and* whatever you're viewing from — needs the Tailscale client
  installed. More private (nothing is exposed to the public internet), but
  requires installing on both ends.
- **Tailscale Funnel**: exposes one local port (CVAT's `:8080`) to the
  public internet via an automatic-HTTPS URL. The rig still needs Tailscale
  installed to set this up (`tailscale funnel 8080`), but **anyone viewing
  the resulting URL just opens it in a browser — no client install on their
  end.** Trade-off: it's genuinely public (anyone with the URL can reach
  it), not just you, so this matters for exactly the kind of production QA
  data CVAT will be holding — worth pairing with CVAT's own login rather
  than treating the URL itself as the only protection.

For solo use where you're the only one tagging, plain Tailscale (client on
both the rig and whatever you're viewing from) is the safer default. Funnel
is the answer specifically if you want to reach it from a device you don't
want to install anything on.

Sources: [Tailscale Funnel docs](https://tailscale.com/docs/features/tailscale-funnel), [tailscale funnel command](https://tailscale.com/kb/1311/tailscale-funnel)

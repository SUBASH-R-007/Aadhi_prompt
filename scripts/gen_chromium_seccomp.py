"""Write ``docker/app/chromium-seccomp.json``: Docker's default seccomp policy plus user namespaces.

    .venv/Scripts/python.exe scripts/gen_chromium_seccomp.py           # rewrite the profile
    .venv/Scripts/python.exe scripts/gen_chromium_seccomp.py --check   # exit 1 if it is stale

Why: the render worker launches Chromium with its sandbox enabled (``aadhi/compose/screenshot.py``,
ARCHITECTURE section 9) as the non-root user ``aadhi``. Chromium's namespace sandbox needs
``clone(CLONE_NEWUSER)``, ``unshare`` and ``setns``; Docker's default profile only allows those
with ``CAP_SYS_ADMIN``, so Chromium aborts with "No usable sandbox!". Granting ``CAP_SYS_ADMIN``
or ``seccomp=unconfined`` would weaken the whole container instead.

The policy below mirrors the structure of Docker's default profile (``moby/profiles/seccomp``):
deny by default with EPERM, an allow-list of ordinary syscalls, capability/arch-conditional rules
(evaluated by dockerd against the container's capability set), ``clone3`` answered with ENOSYS so
glibc falls back to ``clone``, and AF_VSOCK sockets refused. The only addition is the rule that
Playwright documents for running sandboxed Chromium in Docker: allow ``clone``, ``setns`` and
``unshare``. Dangerous syscalls (``mount``, ``bpf``, ``kexec_*``, module loading, ``keyctl``,
``userfaultfd``, ``io_uring_*``, ``perf_event_open`` ...) stay denied.

The host must also allow unprivileged user namespaces (docs/OPERATIONS.md section 4.2);
``docker/app/check_chromium.py`` verifies the result inside the image.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "docker" / "app" / "chromium-seccomp.json"
EPERM, ENOSYS = 1, 38
AF_VSOCK = 40
# CLONE_NEWNS | CLONE_NEWCGROUP | CLONE_NEWUTS | CLONE_NEWIPC | CLONE_NEWUSER | CLONE_NEWPID | CLONE_NEWNET
CLONE_NAMESPACE_FLAGS = 0x7E020000

# Syscalls any unprivileged process may use (Docker's default allow-list).
ALLOWED: tuple[str, ...] = (
    "accept", "accept4", "access", "adjtimex", "alarm", "bind", "brk", "cachestat", "capget", "capset",
    "chdir", "chmod", "chown", "chown32", "clock_adjtime", "clock_adjtime64", "clock_getres",
    "clock_getres_time64", "clock_gettime", "clock_gettime64", "clock_nanosleep", "clock_nanosleep_time64",
    "close", "close_range", "connect", "copy_file_range", "creat", "dup", "dup2", "dup3", "epoll_create",
    "epoll_create1", "epoll_ctl", "epoll_ctl_old", "epoll_pwait", "epoll_pwait2", "epoll_wait",
    "epoll_wait_old", "eventfd", "eventfd2", "execve", "execveat", "exit", "exit_group", "faccessat",
    "faccessat2", "fadvise64", "fadvise64_64", "fallocate", "fanotify_mark", "fchdir", "fchmod",
    "fchmodat", "fchmodat2", "fchown", "fchown32", "fchownat", "fcntl", "fcntl64", "fdatasync",
    "fgetxattr", "flistxattr", "flock", "fork", "fremovexattr", "fsetxattr", "fstat", "fstat64",
    "fstatat64", "fstatfs", "fstatfs64", "fsync", "ftruncate", "ftruncate64", "futex", "futex_requeue",
    "futex_time64", "futex_wait", "futex_waitv", "futex_wake", "futimesat", "get_robust_list",
    "get_thread_area", "getcpu", "getcwd", "getdents", "getdents64", "getegid", "getegid32", "geteuid",
    "geteuid32", "getgid", "getgid32", "getgroups", "getgroups32", "getitimer", "getpeername", "getpgid",
    "getpgrp", "getpid", "getppid", "getpriority", "getrandom", "getresgid", "getresgid32", "getresuid",
    "getresuid32", "getrlimit", "getrusage", "getsid", "getsockname", "getsockopt", "gettid",
    "gettimeofday", "getuid", "getuid32", "getxattr", "inotify_add_watch", "inotify_init",
    "inotify_init1", "inotify_rm_watch", "io_cancel", "io_destroy", "io_getevents", "io_pgetevents",
    "io_pgetevents_time64", "io_setup", "io_submit", "ioctl", "ioprio_get", "ioprio_set", "ipc", "kill",
    "landlock_add_rule", "landlock_create_ruleset", "landlock_restrict_self", "lchown", "lchown32",
    "lgetxattr", "link", "linkat", "listen", "listxattr", "llistxattr", "_llseek", "lremovexattr",
    "lseek", "lsetxattr", "lstat", "lstat64", "madvise", "map_shadow_stack", "membarrier",
    "memfd_create", "memfd_secret", "mincore", "mkdir", "mkdirat", "mknod", "mknodat", "mlock", "mlock2",
    "mlockall", "mmap", "mmap2", "mprotect", "mq_getsetattr", "mq_notify", "mq_open", "mq_timedreceive",
    "mq_timedreceive_time64", "mq_timedsend", "mq_timedsend_time64", "mq_unlink", "mremap", "msgctl",
    "msgget", "msgrcv", "msgsnd", "msync", "munlock", "munlockall", "munmap", "name_to_handle_at",
    "nanosleep", "newfstatat", "_newselect", "open", "openat", "openat2", "pause", "pidfd_open",
    "pidfd_send_signal", "pipe", "pipe2", "pkey_alloc", "pkey_free", "pkey_mprotect", "poll", "ppoll",
    "ppoll_time64", "prctl", "pread64", "preadv", "preadv2", "prlimit64", "process_mrelease", "pselect6",
    "pselect6_time64", "pwrite64", "pwritev", "pwritev2", "read", "readahead", "readlink", "readlinkat",
    "readv", "recv", "recvfrom", "recvmmsg", "recvmmsg_time64", "recvmsg", "remap_file_pages",
    "removexattr", "rename", "renameat", "renameat2", "restart_syscall", "rmdir", "rseq", "rt_sigaction",
    "rt_sigpending", "rt_sigprocmask", "rt_sigqueueinfo", "rt_sigreturn", "rt_sigsuspend",
    "rt_sigtimedwait", "rt_sigtimedwait_time64", "rt_tgsigqueueinfo", "sched_get_priority_max",
    "sched_get_priority_min", "sched_getaffinity", "sched_getattr", "sched_getparam",
    "sched_getscheduler", "sched_rr_get_interval", "sched_rr_get_interval_time64", "sched_setaffinity",
    "sched_setattr", "sched_setparam", "sched_setscheduler", "sched_yield", "seccomp", "select", "semctl",
    "semget", "semop", "semtimedop", "semtimedop_time64", "send", "sendfile", "sendfile64", "sendmmsg",
    "sendmsg", "sendto", "set_robust_list", "set_thread_area", "set_tid_address", "setfsgid",
    "setfsgid32", "setfsuid", "setfsuid32", "setgid", "setgid32", "setgroups", "setgroups32",
    "setitimer", "setpgid", "setpriority", "setregid", "setregid32", "setresgid", "setresgid32",
    "setresuid", "setresuid32", "setreuid", "setreuid32", "setrlimit", "setsid", "setsockopt", "setuid",
    "setuid32", "setxattr", "shmat", "shmctl", "shmdt", "shmget", "shutdown", "sigaltstack", "signalfd",
    "signalfd4", "sigprocmask", "sigreturn", "socketcall", "socketpair", "splice", "stat", "stat64",
    "statfs", "statfs64", "statx", "symlink", "symlinkat", "sync", "sync_file_range", "syncfs", "sysinfo",
    "tee", "tgkill", "time", "timer_create", "timer_delete", "timer_getoverrun", "timer_gettime",
    "timer_gettime64", "timer_settime", "timer_settime64", "timerfd_create", "timerfd_gettime",
    "timerfd_gettime64", "timerfd_settime", "timerfd_settime64", "times", "tkill", "truncate",
    "truncate64", "ugetrlimit", "umask", "uname", "unlink", "unlinkat", "utime", "utimensat",
    "utimensat_time64", "utimes", "vfork", "vmsplice", "wait4", "waitid", "waitpid", "write", "writev",
)

# Syscalls only allowed when the container holds the capability (dockerd evaluates `includes.caps`).
CAPABILITY_GATED: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("CAP_DAC_READ_SEARCH", ("open_by_handle_at",)),
    ("CAP_SYS_ADMIN", (
        "bpf", "clone", "clone3", "fanotify_init", "fsconfig", "fsmount", "fsopen", "fspick",
        "lookup_dcookie", "mount", "mount_setattr", "move_mount", "open_tree", "perf_event_open",
        "quotactl", "quotactl_fd", "setdomainname", "sethostname", "setns", "syslog", "umount", "umount2",
        "unshare",
    )),
    ("CAP_SYS_BOOT", ("reboot",)),
    ("CAP_SYS_CHROOT", ("chroot",)),
    ("CAP_SYS_MODULE", ("delete_module", "finit_module", "init_module")),
    ("CAP_SYS_PACCT", ("acct",)),
    ("CAP_SYS_PTRACE", ("kcmp", "pidfd_getfd", "process_madvise", "process_vm_readv", "process_vm_writev",
                        "ptrace")),
    ("CAP_SYS_RAWIO", ("ioperm", "iopl")),
    ("CAP_SYS_TIME", ("clock_settime", "clock_settime64", "settimeofday", "stime")),
    ("CAP_SYS_TTY_CONFIG", ("vhangup",)),
    ("CAP_SYS_NICE", ("get_mempolicy", "mbind", "set_mempolicy", "set_mempolicy_home_node")),
    ("CAP_SYSLOG", ("syslog",)),
    ("CAP_BPF", ("bpf",)),
    ("CAP_PERFMON", ("perf_event_open",)),
)

# The Chromium addition (Playwright's documented profile): user/pid/net namespaces for the sandbox.
CHROMIUM_NAMESPACE_SYSCALLS = ("clone", "setns", "unshare")

# Never allowed by this profile (checked by tests/evals/test_tooling.py).
ALWAYS_DENIED = (
    "add_key", "keyctl", "request_key", "kexec_file_load", "kexec_load", "userfaultfd", "io_uring_setup",
    "io_uring_enter", "io_uring_register", "pivot_root", "swapon", "swapoff", "uselib", "ustat", "sysfs",
    "_sysctl", "nfsservctl", "vm86", "vm86old", "create_module", "get_kernel_syms", "query_module",
)


def _rule(names: tuple[str, ...] | list[str], action: str = "SCMP_ACT_ALLOW", **extra: Any) -> dict[str, Any]:
    return {"names": sorted(names), "action": action, **extra}


def build_profile() -> dict[str, Any]:
    """The seccomp profile as a JSON-ready dict (deterministic order)."""
    syscalls: list[dict[str, Any]] = [
        _rule(list(ALLOWED)),
        _rule(["process_vm_readv", "process_vm_writev", "ptrace"], includes={"minKernel": "4.8"}),
        _rule(["socket"], args=[{"index": 0, "value": AF_VSOCK, "op": "SCMP_CMP_NE"}]),
    ]
    for persona in (0x0, 0x8, 0x20000, 0x20008, 0xFFFFFFFF):
        syscalls.append(_rule(["personality"], args=[{"index": 0, "value": persona, "op": "SCMP_CMP_EQ"}]))
    syscalls += [
        _rule(["arm_fadvise64_64", "arm_sync_file_range", "breakpoint", "cacheflush", "set_tls",
               "sync_file_range2"], includes={"arches": ["arm", "arm64"]}),
        _rule(["arch_prctl"], includes={"arches": ["amd64", "x32"]}),
        _rule(["modify_ldt"], includes={"arches": ["amd64", "x32", "x86"]}),
    ]
    for cap, names in CAPABILITY_GATED:
        syscalls.append(_rule(names, includes={"caps": [cap]}))
    syscalls += [
        # Without CAP_SYS_ADMIN: clone without namespace flags, and clone3 -> ENOSYS (glibc falls back).
        _rule(["clone"], args=[{"index": 0, "value": CLONE_NAMESPACE_FLAGS, "valueTwo": 0,
                                "op": "SCMP_CMP_MASKED_EQ"}], excludes={"caps": ["CAP_SYS_ADMIN"]}),
        _rule(["clone3"], action="SCMP_ACT_ERRNO", errnoRet=ENOSYS, excludes={"caps": ["CAP_SYS_ADMIN"]}),
        {"comment": "Chromium sandbox: allow creating user namespaces (Playwright's Docker profile)",
         **_rule(list(CHROMIUM_NAMESPACE_SYSCALLS))},
    ]
    return {
        "defaultAction": "SCMP_ACT_ERRNO",
        "defaultErrnoRet": EPERM,
        "archMap": [
            {"architecture": "SCMP_ARCH_X86_64", "subArchitectures": ["SCMP_ARCH_X86", "SCMP_ARCH_X32"]},
            {"architecture": "SCMP_ARCH_AARCH64", "subArchitectures": ["SCMP_ARCH_ARM"]},
        ],
        "syscalls": syscalls,
    }


def render() -> str:
    """Profile text exactly as committed (LF line endings, trailing newline)."""
    return json.dumps(build_profile(), indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if the committed profile is stale")
    parser.add_argument("--output", type=Path, default=PROFILE)
    args = parser.parse_args(argv)
    text = render()
    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.is_file() else ""
        if current.replace("\r\n", "\n") != text:
            print(f"{args.output.name} is stale: run scripts/gen_chromium_seccomp.py")
            return 1
        print(f"{args.output.name} is up to date")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

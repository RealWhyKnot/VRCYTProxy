import ctypes
import platform
import struct
import logging

logger = logging.getLogger("JobManager")

class JobManager:
    def __init__(self):
        self.job_handle = None
        if platform.system() == 'Windows':
            try:
                self.job_handle = ctypes.windll.kernel32.CreateJobObjectW(None, None)
                info = ctypes.create_string_buffer(1024)
                ctypes.memset(info, 0, 1024)
                # 0x2000 = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                struct.pack_into("Q", info, 16, 0x2000) 
                # 9 = JobObjectExtendedLimitInformation
                ctypes.windll.kernel32.SetInformationJobObject(self.job_handle, 9, info, 144)
            except Exception:
                self.job_handle = None

    def assign(self, process):
        if self.job_handle and process:
            try:
                ctypes.windll.kernel32.AssignProcessToJobObject(self.job_handle, int(process._handle))
            except Exception: pass

    def close(self):
        if self.job_handle:
            try:
                ctypes.windll.kernel32.CloseHandle(self.job_handle)
                self.job_handle = None
            except Exception: pass

job_manager = JobManager()

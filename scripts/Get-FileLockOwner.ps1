[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Path)

$source = @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

public static class RestartManagerLockFinder {
    const int CCH_RM_MAX_APP_NAME = 255;
    const int CCH_RM_MAX_SVC_NAME = 63;

    [StructLayout(LayoutKind.Sequential)]
    struct RM_UNIQUE_PROCESS {
        public int dwProcessId;
        public System.Runtime.InteropServices.ComTypes.FILETIME ProcessStartTime;
    }

    enum RM_APP_TYPE {
        RmUnknownApp = 0, RmMainWindow = 1, RmOtherWindow = 2,
        RmService = 3, RmExplorer = 4, RmConsole = 5, RmCritical = 1000
    }

    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
    struct RM_PROCESS_INFO {
        public RM_UNIQUE_PROCESS Process;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=CCH_RM_MAX_APP_NAME + 1)]
        public string strAppName;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=CCH_RM_MAX_SVC_NAME + 1)]
        public string strServiceShortName;
        public RM_APP_TYPE ApplicationType;
        public uint AppStatus;
        public uint TSSessionId;
        [MarshalAs(UnmanagedType.Bool)] public bool bRestartable;
    }

    [DllImport("rstrtmgr.dll", CharSet=CharSet.Unicode)]
    static extern int RmStartSession(out uint handle, int flags, string key);
    [DllImport("rstrtmgr.dll", CharSet=CharSet.Unicode)]
    static extern int RmRegisterResources(
        uint handle, uint filesCount, string[] files,
        uint appsCount, IntPtr apps, uint servicesCount, string[] services);
    [DllImport("rstrtmgr.dll")]
    static extern int RmGetList(
        uint handle, out uint needed, ref uint count,
        [In, Out] RM_PROCESS_INFO[] infos, ref uint rebootReasons);
    [DllImport("rstrtmgr.dll")]
    static extern int RmEndSession(uint handle);

    public static string[] Find(string path) {
        uint handle;
        int rc = RmStartSession(out handle, 0, Guid.NewGuid().ToString());
        if (rc != 0) return new[] { "Restart Manager error " + rc };
        try {
            rc = RmRegisterResources(handle, 1, new[] { path }, 0, IntPtr.Zero, 0, null);
            if (rc != 0) return new[] { "Restart Manager registration error " + rc };
            uint needed = 0, count = 0, reasons = 0;
            rc = RmGetList(handle, out needed, ref count, null, ref reasons);
            if (rc == 0) return Array.Empty<string>();
            if (rc != 234) return new[] { "Restart Manager list error " + rc };
            var infos = new RM_PROCESS_INFO[needed];
            count = needed;
            rc = RmGetList(handle, out needed, ref count, infos, ref reasons);
            if (rc != 0) return new[] { "Restart Manager list error " + rc };
            var result = new List<string>();
            for (int i = 0; i < count; i++) {
                result.Add(
                    infos[i].Process.dwProcessId + "|" +
                    infos[i].strAppName + "|" +
                    infos[i].strServiceShortName
                );
            }
            return result.ToArray();
        } finally {
            RmEndSession(handle);
        }
    }
}
'@

if (-not ("RestartManagerLockFinder" -as [type])) {
    Add-Type -TypeDefinition $source
}
[RestartManagerLockFinder]::Find((Resolve-Path -LiteralPath $Path).Path)

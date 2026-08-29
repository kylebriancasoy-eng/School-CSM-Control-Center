using System;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using Microsoft.Win32;

namespace MoSSLab.SchoolCSM.Installer
{
    internal static class WindowsIntegration
    {
        private const string UninstallKey = @"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\" + InstallerEngine.ApplicationId;
        private const string StartupRunKey = @"SOFTWARE\Microsoft\Windows\CurrentVersion\Run";
        private const string StartupValueName = "MoSSLab.SchoolCSMControlCenter";
        private const string FirewallRuleName = "School CSM Control Center - Private Local Survey";
        private const int FirewallProtocolAny = 256;
        private const int FirewallDirectionInbound = 1;
        private const int FirewallProfilePrivate = 2;
        private const int FirewallActionAllow = 1;
        private const uint CredentialTypeGeneric = 1;

        private static readonly string[] LegacyFirewallRuleNames =
        {
            "School CSM Control Center TCP 80",
            "School CSM Control Center TCP 8080",
            "School CSM Control Center TCP 53",
            "School CSM Control Center UDP 53"
        };

        [DllImport("advapi32.dll", EntryPoint = "CredDeleteW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool CredDelete(string target, uint type, uint flags);

        internal static string ReadInstalledVersion()
        {
            using (RegistryKey key = Registry.LocalMachine.OpenSubKey(UninstallKey, false))
            {
                return key == null ? String.Empty : Convert.ToString(key.GetValue("DisplayVersion"));
            }
        }

        internal static void WriteUninstallRegistration(string version, long estimatedBytes)
        {
            string maintenanceExe = InstallerEngine.MaintenanceExecutable;
            string appExe = InstallerEngine.ApplicationExecutable;
            using (RegistryKey key = Registry.LocalMachine.CreateSubKey(UninstallKey))
            {
                if (key == null)
                {
                    throw new InvalidOperationException("Windows could not create the uninstall registration.");
                }
                key.SetValue("DisplayName", InstallerEngine.DisplayName, RegistryValueKind.String);
                key.SetValue("DisplayVersion", version, RegistryValueKind.String);
                key.SetValue("Publisher", "MoSSLab", RegistryValueKind.String);
                key.SetValue("InstallLocation", InstallerEngine.InstallRoot, RegistryValueKind.String);
                key.SetValue("DisplayIcon", appExe + ",0", RegistryValueKind.String);
                key.SetValue("UninstallString", Quote(maintenanceExe) + " --uninstall", RegistryValueKind.String);
                key.SetValue("QuietUninstallString", Quote(maintenanceExe) + " --uninstall --quiet", RegistryValueKind.String);
                key.SetValue("ModifyPath", Quote(maintenanceExe), RegistryValueKind.String);
                key.SetValue("URLInfoAbout", "https://github.com/" + BuildConfig.Repository, RegistryValueKind.String);
                key.SetValue("NoModify", 0, RegistryValueKind.DWord);
                key.SetValue("NoRepair", 0, RegistryValueKind.DWord);
                long kiloBytes = Math.Max(1, estimatedBytes / 1024);
                key.SetValue("EstimatedSize", Math.Min(Int32.MaxValue, kiloBytes), RegistryValueKind.DWord);
            }
        }

        internal static void RemoveUninstallRegistration()
        {
            try
            {
                Registry.LocalMachine.DeleteSubKeyTree(UninstallKey, false);
            }
            catch (ArgumentException)
            {
            }
        }

        internal static void CreateShortcuts()
        {
            string startMenuDirectory = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.CommonPrograms), "MoSSLab");
            Directory.CreateDirectory(startMenuDirectory);
            CreateShortcut(
                Path.Combine(startMenuDirectory, InstallerEngine.DisplayName + ".lnk"),
                InstallerEngine.ApplicationExecutable,
                InstallerEngine.InstallRoot,
                InstallerEngine.ApplicationExecutable);
            CreateShortcut(
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonDesktopDirectory), InstallerEngine.DisplayName + ".lnk"),
                InstallerEngine.ApplicationExecutable,
                InstallerEngine.InstallRoot,
                InstallerEngine.ApplicationExecutable);
        }

        internal static void RemoveShortcuts()
        {
            DeleteIfPresent(Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.CommonPrograms),
                "MoSSLab",
                InstallerEngine.DisplayName + ".lnk"));
            DeleteIfPresent(Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.CommonDesktopDirectory),
                InstallerEngine.DisplayName + ".lnk"));
        }

        internal static void ConfigureFirewallRule()
        {
            if (!File.Exists(InstallerEngine.ApplicationExecutable))
            {
                throw new FileNotFoundException(
                    "Windows Firewall access cannot be configured until the verified application executable is installed.",
                    InstallerEngine.ApplicationExecutable);
            }

            object policy = null;
            object rules = null;
            object rule = null;
            try
            {
                rules = OpenFirewallRules(out policy);
                RemoveFirewallRulesCore(rules);

                Type ruleType = Type.GetTypeFromProgID("HNetCfg.FWRule", true);
                rule = Activator.CreateInstance(ruleType);
                SetComProperty(rule, "Name", FirewallRuleName);
                SetComProperty(
                    rule,
                    "Description",
                    "Allows the installed School CSM Control Center to serve respondents only from the active private local subnet.");
                SetComProperty(rule, "ApplicationName", InstallerEngine.ApplicationExecutable);
                SetComProperty(rule, "Protocol", FirewallProtocolAny);
                SetComProperty(rule, "Direction", FirewallDirectionInbound);
                SetComProperty(rule, "Enabled", true);
                SetComProperty(rule, "Profiles", FirewallProfilePrivate);
                SetComProperty(rule, "RemoteAddresses", "LocalSubnet");
                SetComProperty(rule, "EdgeTraversal", false);
                SetComProperty(rule, "Action", FirewallActionAllow);
                InvokeComMethod(rules, "Add", rule);
            }
            catch (Exception error)
            {
                throw new InvalidOperationException(
                    "Windows could not configure the private-local-subnet firewall rule for the installed application.",
                    UnwrapInvocationException(error));
            }
            finally
            {
                ReleaseComObject(rule);
                ReleaseComObject(rules);
                ReleaseComObject(policy);
            }
        }

        internal static void RemoveFirewallRules()
        {
            object policy = null;
            object rules = null;
            try
            {
                rules = OpenFirewallRules(out policy);
                RemoveFirewallRulesCore(rules);
            }
            catch (Exception error)
            {
                throw new InvalidOperationException(
                    "Windows could not remove the School CSM Control Center firewall rules.",
                    UnwrapInvocationException(error));
            }
            finally
            {
                ReleaseComObject(rules);
                ReleaseComObject(policy);
            }
        }

        internal static void RemoveCurrentUserStartupRegistration()
        {
            using (RegistryKey key = Registry.CurrentUser.OpenSubKey(StartupRunKey, true))
            {
                if (key != null)
                {
                    key.DeleteValue(StartupValueName, false);
                }
            }
        }

        internal static bool DeleteCurrentUserCredentials()
        {
            string[] targets =
            {
                InstallerEngine.OpenAiCredentialTarget,
                InstallerEngine.GatewayTunnelCredentialTarget,
                InstallerEngine.GatewayInstallationSecretTarget,
                InstallerEngine.GatewayDevicePrivateKeyTarget
            };
            bool success = true;
            foreach (string target in targets)
            {
                bool deleted = CredDelete(target, CredentialTypeGeneric, 0);
                int error = Marshal.GetLastWin32Error();
                success = success && (deleted || error == 1168);
            }
            return success;
        }

        private static object OpenFirewallRules(out object policy)
        {
            Type policyType = Type.GetTypeFromProgID("HNetCfg.FwPolicy2", true);
            policy = Activator.CreateInstance(policyType);
            return policy.GetType().InvokeMember(
                "Rules",
                BindingFlags.GetProperty,
                null,
                policy,
                null);
        }

        private static void RemoveFirewallRulesCore(object rules)
        {
            RemoveFirewallRuleIfPresent(rules, FirewallRuleName);
            foreach (string legacyRuleName in LegacyFirewallRuleNames)
            {
                RemoveFirewallRuleIfPresent(rules, legacyRuleName);
            }
        }

        private static void RemoveFirewallRuleIfPresent(object rules, string name)
        {
            try
            {
                InvokeComMethod(rules, "Remove", name);
            }
            catch (Exception error)
            {
                Exception unwrapped = UnwrapInvocationException(error);
                COMException comError = unwrapped as COMException;
                if (comError != null && IsMissingFirewallRule(comError.ErrorCode))
                {
                    return;
                }
                throw;
            }
        }

        private static bool IsMissingFirewallRule(int errorCode)
        {
            return errorCode == unchecked((int)0x80070002) ||
                errorCode == unchecked((int)0x80070490);
        }

        private static void SetComProperty(object target, string name, object value)
        {
            target.GetType().InvokeMember(
                name,
                BindingFlags.SetProperty,
                null,
                target,
                new object[] { value });
        }

        private static object InvokeComMethod(object target, string name, object argument)
        {
            return target.GetType().InvokeMember(
                name,
                BindingFlags.InvokeMethod,
                null,
                target,
                new object[] { argument });
        }

        private static Exception UnwrapInvocationException(Exception error)
        {
            TargetInvocationException invocation = error as TargetInvocationException;
            return invocation != null && invocation.InnerException != null
                ? invocation.InnerException
                : error;
        }

        private static void ReleaseComObject(object value)
        {
            if (value != null && Marshal.IsComObject(value))
            {
                Marshal.FinalReleaseComObject(value);
            }
        }

        private static void DeleteIfPresent(string path)
        {
            try
            {
                if (File.Exists(path))
                {
                    File.Delete(path);
                }
            }
            catch (IOException)
            {
            }
            catch (UnauthorizedAccessException)
            {
            }
        }

        private static string Quote(string value)
        {
            return "\"" + value.Replace("\"", "\\\"") + "\"";
        }

        private static void CreateShortcut(string shortcutPath, string target, string workingDirectory, string icon)
        {
            IShellLinkW link = (IShellLinkW)new ShellLink();
            link.SetPath(target);
            link.SetWorkingDirectory(workingDirectory);
            link.SetDescription(InstallerEngine.DisplayName);
            link.SetIconLocation(icon, 0);
            ((IPersistFile)link).Save(shortcutPath, true);
            Marshal.FinalReleaseComObject(link);
        }

        [ComImport]
        [Guid("00021401-0000-0000-C000-000000000046")]
        private class ShellLink
        {
        }

        [ComImport]
        [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        [Guid("000214F9-0000-0000-C000-000000000046")]
        private interface IShellLinkW
        {
            void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder file, int maximum, IntPtr findData, uint flags);
            void GetIDList(out IntPtr itemIdList);
            void SetIDList(IntPtr itemIdList);
            void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder description, int maximum);
            void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string description);
            void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder directory, int maximum);
            void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string directory);
            void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder arguments, int maximum);
            void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string arguments);
            void GetHotkey(out short hotkey);
            void SetHotkey(short hotkey);
            void GetShowCmd(out int showCommand);
            void SetShowCmd(int showCommand);
            void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder iconPath, int maximum, out int iconIndex);
            void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string iconPath, int iconIndex);
            void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string path, uint reserved);
            void Resolve(IntPtr windowHandle, uint flags);
            void SetPath([MarshalAs(UnmanagedType.LPWStr)] string path);
        }

        [ComImport]
        [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        [Guid("0000010b-0000-0000-C000-000000000046")]
        private interface IPersistFile
        {
            void GetClassID(out Guid classId);
            [PreserveSig]
            int IsDirty();
            void Load([MarshalAs(UnmanagedType.LPWStr)] string fileName, uint mode);
            void Save([MarshalAs(UnmanagedType.LPWStr)] string fileName, bool remember);
            void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string fileName);
            void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string fileName);
        }
    }
}

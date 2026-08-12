using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;

namespace MoSSLab.SchoolCSM.Installer
{
    internal sealed class InstallerEngine
    {
        internal const string ApplicationId = "MoSSLab.SchoolCSMControlCenter";
        internal const string DisplayName = "School CSM Control Center";
        internal const string ApplicationExeName = "School CSM Control Center.exe";
        internal const string SetupExeName = "School-CSM-Control-Center-Setup.exe";
        internal const string OpenAiCredentialTarget = "MoSSLab.SchoolCSMControlCenter.OpenAIApiKey";

        private const long MaximumPackageBytes = 2L * 1024 * 1024 * 1024;
        private const long MaximumExtractedBytes = 4L * 1024 * 1024 * 1024;
        private const int MaximumManifestBytes = 1024 * 1024;
        private const int MaximumArchiveEntries = 200000;
        private static readonly object LogLock = new object();

        internal static readonly string VendorProgramFilesRoot = Path.Combine(ProgramFilesX86(), "MoSSLab");
        internal static readonly string InstallRoot = Path.Combine(VendorProgramFilesRoot, DisplayName);
        internal static readonly string ApplicationExecutable = Path.Combine(InstallRoot, ApplicationExeName);
        internal static readonly string MaintenanceRoot = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),
            "MoSSLab",
            DisplayName,
            "Maintenance");
        internal static readonly string MaintenanceExecutable = Path.Combine(MaintenanceRoot, SetupExeName);
        internal static readonly string StagingRoot = Path.Combine(MaintenanceRoot, "staging");
        internal static readonly string RollbackRoot = Path.Combine(MaintenanceRoot, "rollback");
        internal static readonly string InstalledManifestPath = Path.Combine(MaintenanceRoot, "installed-release.json");
        internal static readonly string InstalledPackagePath = Path.Combine(MaintenanceRoot, "installed-package.zip");
        internal static readonly string PreviousManifestPath = Path.Combine(RollbackRoot, "previous-release.json");
        internal static readonly string PreviousPackagePath = Path.Combine(RollbackRoot, "previous-package.zip");
        internal static readonly string MaintenanceLogPath = Path.Combine(MaintenanceRoot, "maintenance.log");

        internal event Action<string> StatusChanged;

        internal void RecordFailure(Exception error)
        {
            string message = error == null ? "Unknown error." : error.Message;
            if (String.IsNullOrWhiteSpace(message))
            {
                message = "Unknown error.";
            }
            message = message.Replace('\r', ' ').Replace('\n', ' ').Trim();
            if (message.Length > 500)
            {
                message = message.Substring(0, 500) + "...";
            }
            Report("Maintenance operation failed: " + message);
        }

        internal bool IsInstalled
        {
            get { return File.Exists(ApplicationExecutable); }
        }

        internal bool CanRollback
        {
            get
            {
                return File.Exists(PreviousPackagePath) && File.Exists(PreviousManifestPath);
            }
        }

        internal string InstalledVersion
        {
            get
            {
                string version = WindowsIntegration.ReadInstalledVersion();
                return String.IsNullOrWhiteSpace(version) ? "Not installed" : version;
            }
        }

        internal void InstallOrUpdate()
        {
            EnsureApplicationClosed();
            ReleaseManifest manifest = DownloadLatestManifest();
            string currentText = WindowsIntegration.ReadInstalledVersion();
            Version current;
            if (Version.TryParse(currentText, out current) && current > manifest.ParsedVersion)
            {
                throw new InvalidOperationException(
                    "The installed version is newer than the latest published release. " +
                    "Use Repair to restore the installed version instead.");
            }
            if (IsInstalled && current != null && current == manifest.ParsedVersion)
            {
                PersistCurrentSetup();
                Report("Refreshing private-network firewall access for the installed application...");
                WindowsIntegration.ConfigureFirewallRule();
                WindowsIntegration.WriteUninstallRegistration(currentText, DirectorySize(InstallRoot));
                Report("Version " + manifest.VersionText + " is already installed. Use Repair if its files need to be restored.");
                return;
            }
            DeployDownloadedRelease(manifest, IsInstalled ? "Updating" : "Installing", true);
        }

        internal void Repair()
        {
            if (!IsInstalled)
            {
                throw new InvalidOperationException("The application is not installed. Choose Install first.");
            }
            EnsureApplicationClosed();
            ReleaseManifest manifest;
            if (File.Exists(InstalledManifestPath))
            {
                manifest = ReleaseManifest.Read(File.ReadAllBytes(InstalledManifestPath));
                manifest.Validate(BuildConfig.Repository);
            }
            else
            {
                manifest = DownloadLatestManifest();
                string installed = WindowsIntegration.ReadInstalledVersion();
                if (!String.Equals(installed, manifest.VersionText, StringComparison.Ordinal))
                {
                    throw new InvalidOperationException(
                        "The exact installed release information is unavailable. Choose Update to install " +
                        "the latest verified release.");
                }
            }
            DeployDownloadedRelease(manifest, "Repairing", false);
        }

        internal void Rollback()
        {
            if (!IsInstalled || !CanRollback)
            {
                throw new InvalidOperationException("No previous verified installation is available to restore.");
            }
            EnsureApplicationClosed();
            ReleaseManifest previous = ReleaseManifest.Read(File.ReadAllBytes(PreviousManifestPath));
            previous.Validate(BuildConfig.Repository);

            string working = CreateUniqueDirectory(StagingRoot, "rollback");
            string archiveCopy = Path.Combine(working, previous.Package.FileName);
            string sourceCopy = Path.Combine(working, "extracted");
            try
            {
                Report("Validating the previous installation...");
                File.Copy(PreviousPackagePath, archiveCopy, false);
                VerifyLocalPackage(archiveCopy, previous.Package);
                ExtractCheckedZip(archiveCopy, sourceCopy);
                VerifyExtractedEntryPoint(sourceCopy, previous.Package);
                DeployDirectory(sourceCopy, previous, "Rolling back", true);
                SaveInstalledPackage(archiveCopy);
            }
            finally
            {
                DeleteWithin(working, StagingRoot);
            }
        }

        internal void Uninstall(bool removeUserData)
        {
            EnsureApplicationClosed();
            Report("Preparing to remove the application...");
            PreserveLegacyMutableData();
            try
            {
                Report("Removing application-owned Windows startup and firewall entries...");
                WindowsIntegration.RemoveFirewallRules();
                WindowsIntegration.RemoveCurrentUserStartupRegistration();
            }
            catch
            {
                if (File.Exists(ApplicationExecutable))
                {
                    try
                    {
                        WindowsIntegration.ConfigureFirewallRule();
                    }
                    catch
                    {
                    }
                }
                throw;
            }
            if (Directory.Exists(InstallRoot))
            {
                DeleteWithin(InstallRoot, VendorProgramFilesRoot);
            }
            WindowsIntegration.RemoveShortcuts();
            WindowsIntegration.RemoveUninstallRegistration();

            if (removeUserData)
            {
                string dataRoot = StandardUserDataRoot();
                Report("Removing saved records for the current Windows account...");
                DeleteExactUserData(dataRoot);
                if (!WindowsIntegration.DeleteOpenAiCredential())
                {
                    throw new InvalidOperationException(
                        "The application was removed, but Windows could not remove the saved OpenAI API key.");
                }
            }
            else
            {
                Report("Saved records and credentials were preserved.");
            }

            CleanupMaintenanceCache();
            Report("Uninstall completed.");
        }

        internal void LaunchApplication()
        {
            if (!File.Exists(ApplicationExecutable))
            {
                throw new FileNotFoundException("The application executable is not installed.", ApplicationExecutable);
            }
            Process.Start(new ProcessStartInfo
            {
                FileName = ApplicationExecutable,
                WorkingDirectory = InstallRoot,
                UseShellExecute = true
            });
        }

        private void DeployDownloadedRelease(ReleaseManifest manifest, string operation, bool rotateRollback)
        {
            string working = CreateUniqueDirectory(StagingRoot, "release");
            string archivePath = Path.Combine(working, manifest.Package.FileName);
            string extractedPath = Path.Combine(working, "extracted");
            try
            {
                Report(operation + " version " + manifest.VersionText + "...");
                DownloadPackage(manifest.Package, archivePath);
                Report("Extracting the verified package...");
                ExtractCheckedZip(archivePath, extractedPath);
                VerifyExtractedEntryPoint(extractedPath, manifest.Package);
                DeployDirectory(extractedPath, manifest, operation, rotateRollback);
                SaveInstalledPackage(archivePath);
            }
            finally
            {
                DeleteWithin(working, StagingRoot);
            }
        }

        private void DeployDirectory(string source, ReleaseManifest manifest, string operation, bool rotateRollback)
        {
            PreserveLegacyMutableData();
            Directory.CreateDirectory(VendorProgramFilesRoot);
            Directory.CreateDirectory(MaintenanceRoot);
            PersistCurrentSetup();

            string identifier = Guid.NewGuid().ToString("N");
            string programStage = Path.Combine(VendorProgramFilesRoot, ".School-CSM-Control-Center-stage-" + identifier);
            string displaced = Path.Combine(VendorProgramFilesRoot, ".School-CSM-Control-Center-old-" + identifier);
            string currentManifestCandidate = Path.Combine(RollbackRoot, ".candidate-" + identifier + ".json");
            string currentPackageCandidate = Path.Combine(RollbackRoot, ".candidate-" + identifier + ".zip");
            string restoreManifestCandidate = Path.Combine(MaintenanceRoot, ".restore-" + identifier + ".json");
            string previousVersion = WindowsIntegration.ReadInstalledVersion();
            bool hadPreviousInstallation = Directory.Exists(InstallRoot);
            bool displacedCurrent = false;
            bool installedNew = false;
            try
            {
                Report("Staging application files in Program Files...");
                CopyDirectory(source, programStage);
                VerifyExtractedEntryPoint(programStage, manifest.Package);

                if (Directory.Exists(InstallRoot))
                {
                    if (File.Exists(InstalledManifestPath))
                    {
                        File.Copy(InstalledManifestPath, restoreManifestCandidate, true);
                    }
                    if (rotateRollback)
                    {
                        Directory.CreateDirectory(RollbackRoot);
                        if (File.Exists(InstalledManifestPath) && File.Exists(InstalledPackagePath))
                        {
                            try
                            {
                                ReleaseManifest currentManifest = ReleaseManifest.Read(File.ReadAllBytes(InstalledManifestPath));
                                currentManifest.Validate(BuildConfig.Repository);
                                VerifyLocalPackage(InstalledPackagePath, currentManifest.Package);
                                Report("Saving the verified package for rollback outside the installation folder...");
                                File.Copy(restoreManifestCandidate, currentManifestCandidate, true);
                                File.Copy(InstalledPackagePath, currentPackageCandidate, true);
                            }
                            catch (Exception cacheError)
                            {
                                DeleteFileIfPresent(currentManifestCandidate);
                                DeleteFileIfPresent(currentPackageCandidate);
                                Report("The existing package cache is not valid for rollback and was skipped: " + cacheError.Message);
                            }
                        }
                    }
                    Directory.Move(InstallRoot, displaced);
                    displacedCurrent = true;
                }

                Directory.Move(programStage, InstallRoot);
                installedNew = true;
                VerifyExtractedEntryPoint(InstallRoot, manifest.Package);
                Report("Configuring private-local-subnet firewall access for the verified application...");
                WindowsIntegration.ConfigureFirewallRule();
                File.WriteAllBytes(Path.Combine(InstallRoot, ".release.json"), manifest.ToBytes());
                WriteBytesAtomically(InstalledManifestPath, manifest.ToBytes());
                WindowsIntegration.CreateShortcuts();
                WindowsIntegration.WriteUninstallRegistration(manifest.VersionText, DirectorySize(InstallRoot));

                try
                {
                    PromoteRollback(currentManifestCandidate, currentPackageCandidate);
                }
                catch (Exception cacheError)
                {
                    Report("The application was installed, but the optional rollback cache could not be updated: " + cacheError.Message);
                }
                if (Directory.Exists(displaced))
                {
                    DeleteWithin(displaced, VendorProgramFilesRoot);
                }
                Report(operation + " completed successfully.");
            }
            catch
            {
                Report("The operation failed. Restoring the previous installation...");
                try
                {
                    if (installedNew && Directory.Exists(InstallRoot))
                    {
                        DeleteWithin(InstallRoot, VendorProgramFilesRoot);
                    }
                    if (displacedCurrent && Directory.Exists(displaced))
                    {
                        Directory.Move(displaced, InstallRoot);
                    }
                    if (hadPreviousInstallation && Directory.Exists(InstallRoot))
                    {
                        if (File.Exists(restoreManifestCandidate))
                        {
                            File.Copy(restoreManifestCandidate, InstalledManifestPath, true);
                        }
                        else
                        {
                            DeleteFileIfPresent(InstalledManifestPath);
                        }
                        WindowsIntegration.ConfigureFirewallRule();
                        WindowsIntegration.CreateShortcuts();
                        WindowsIntegration.WriteUninstallRegistration(previousVersion, DirectorySize(InstallRoot));
                    }
                    else
                    {
                        WindowsIntegration.RemoveFirewallRules();
                        DeleteFileIfPresent(InstalledManifestPath);
                        WindowsIntegration.RemoveShortcuts();
                        WindowsIntegration.RemoveUninstallRegistration();
                    }
                }
                catch (Exception rollbackError)
                {
                    Report("Automatic rollback also encountered an error: " + rollbackError.Message);
                }
                throw;
            }
            finally
            {
                DeleteWithin(programStage, VendorProgramFilesRoot);
                DeleteWithin(displaced, VendorProgramFilesRoot);
                DeleteFileIfPresent(currentManifestCandidate);
                DeleteFileIfPresent(currentPackageCandidate);
                DeleteFileIfPresent(restoreManifestCandidate);
            }
        }

        private void PromoteRollback(string candidateManifest, string candidatePackage)
        {
            if (!File.Exists(candidateManifest) || !File.Exists(candidatePackage))
            {
                return;
            }
            DeleteFileIfPresent(PreviousManifestPath);
            DeleteFileIfPresent(PreviousPackagePath);
            File.Move(candidateManifest, PreviousManifestPath);
            File.Move(candidatePackage, PreviousPackagePath);
        }

        private ReleaseManifest DownloadLatestManifest()
        {
            Report("Checking the latest GitHub release...");
            ResilientDownloader downloader = new ResilientDownloader(Report);
            byte[] bytes = downloader.DownloadBytes(
                BuildConfig.ManifestUrl,
                MaximumManifestBytes,
                "the release manifest");
            ReleaseManifest manifest = ReleaseManifest.Read(bytes);
            manifest.Validate(BuildConfig.Repository);
            return manifest;
        }

        private void DownloadPackage(ReleaseAsset package, string destination)
        {
            if (package.SizeBytes > MaximumPackageBytes)
            {
                throw new InvalidDataException("The published package is unexpectedly large.");
            }
            Report("Downloading " + package.FileName + "...");
            ResilientDownloader downloader = new ResilientDownloader(Report);
            downloader.DownloadFile(
                package.Url,
                destination,
                package.SizeBytes,
                "the application package");
            FileInfo info = new FileInfo(destination);
            if (info.Length != package.SizeBytes)
            {
                throw new InvalidDataException("The downloaded package size does not match the release manifest.");
            }
            string actual = Hashing.Sha256File(destination);
            if (!String.Equals(actual, package.Sha256, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidDataException("The downloaded package failed SHA-256 verification.");
            }
            Report("Package checksum verified.");
        }

        private static void VerifyLocalPackage(string path, ReleaseAsset package)
        {
            FileInfo info = new FileInfo(path);
            if (!info.Exists || info.Length != package.SizeBytes)
            {
                throw new InvalidDataException("A cached release package has an unexpected size.");
            }
            if (!String.Equals(Hashing.Sha256File(path), package.Sha256, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidDataException("A cached release package failed SHA-256 verification.");
            }
        }

        private void SaveInstalledPackage(string source)
        {
            string temporary = InstalledPackagePath + ".new-" + Guid.NewGuid().ToString("N");
            try
            {
                Directory.CreateDirectory(MaintenanceRoot);
                File.Copy(source, temporary, false);
                if (File.Exists(InstalledPackagePath))
                {
                    File.Replace(temporary, InstalledPackagePath, null);
                }
                else
                {
                    File.Move(temporary, InstalledPackagePath);
                }
            }
            catch (Exception cacheError)
            {
                DeleteFileIfPresent(temporary);
                DeleteFileIfPresent(InstalledPackagePath);
                Report("The application is ready, but its optional rollback package could not be cached: " + cacheError.Message);
            }
        }

        private static void ExtractCheckedZip(string archivePath, string destination)
        {
            Directory.CreateDirectory(destination);
            string destinationPrefix = EnsureTrailingSeparator(Path.GetFullPath(destination));
            long declaredBytes = 0;
            long actualBytes = 0;
            byte[] copyBuffer = new byte[1024 * 1024];
            using (ZipArchive archive = ZipFile.OpenRead(archivePath))
            {
                if (archive.Entries.Count > MaximumArchiveEntries)
                {
                    throw new InvalidDataException("The package contains unexpectedly many files.");
                }
                HashSet<string> outputPaths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                foreach (ZipArchiveEntry entry in archive.Entries)
                {
                    ValidateArchiveEntry(entry);
                    string normalizedName = entry.FullName.Replace('/', Path.DirectorySeparatorChar);
                    string outputPath = Path.GetFullPath(Path.Combine(destination, normalizedName));
                    if (!outputPath.StartsWith(destinationPrefix, StringComparison.OrdinalIgnoreCase))
                    {
                        throw new InvalidDataException("The package contains an unsafe parent path.");
                    }
                    if (!outputPaths.Add(outputPath.TrimEnd(Path.DirectorySeparatorChar)))
                    {
                        throw new InvalidDataException("The package contains duplicate file paths.");
                    }
                    if (String.IsNullOrEmpty(entry.Name))
                    {
                        Directory.CreateDirectory(outputPath);
                        continue;
                    }
                    if (entry.Length < 0 || declaredBytes > MaximumExtractedBytes - entry.Length)
                    {
                        throw new InvalidDataException("The uncompressed package is unexpectedly large.");
                    }
                    declaredBytes += entry.Length;
                    string parent = Path.GetDirectoryName(outputPath);
                    if (!String.IsNullOrEmpty(parent))
                    {
                        Directory.CreateDirectory(parent);
                    }
                    using (Stream input = entry.Open())
                    using (FileStream output = new FileStream(outputPath, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                    {
                        int read;
                        while ((read = input.Read(copyBuffer, 0, copyBuffer.Length)) > 0)
                        {
                            actualBytes += read;
                            if (actualBytes > MaximumExtractedBytes)
                            {
                                throw new InvalidDataException("The package expanded beyond the safe extraction limit.");
                            }
                            output.Write(copyBuffer, 0, read);
                        }
                    }
                }
            }
        }

        private static void ValidateArchiveEntry(ZipArchiveEntry entry)
        {
            string name = entry.FullName ?? String.Empty;
            if (String.IsNullOrWhiteSpace(name) || name.IndexOf('\\') >= 0 || name.IndexOf('\0') >= 0)
            {
                throw new InvalidDataException("The package contains an invalid file path.");
            }
            string normalized = name.TrimEnd('/');
            if (String.IsNullOrWhiteSpace(normalized) || Path.IsPathRooted(normalized) || normalized.IndexOf(':') >= 0)
            {
                throw new InvalidDataException("The package contains an unsafe absolute path.");
            }
            foreach (string segment in normalized.Split('/'))
            {
                if (String.IsNullOrEmpty(segment) || segment == "." || segment == ".." ||
                    segment.EndsWith(" ", StringComparison.Ordinal) || segment.EndsWith(".", StringComparison.Ordinal) ||
                    segment.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 || IsReservedWindowsName(segment))
                {
                    throw new InvalidDataException("The package contains an unsafe file-name segment.");
                }
            }

            const int UnixFileTypeMask = 0xF000;
            const int UnixSymbolicLink = 0xA000;
            const int DosReparsePoint = 0x0400;
            int unixMode = (entry.ExternalAttributes >> 16) & 0xFFFF;
            if ((unixMode & UnixFileTypeMask) == UnixSymbolicLink ||
                (entry.ExternalAttributes & DosReparsePoint) != 0)
            {
                throw new InvalidDataException("The package contains a symbolic link or reparse point.");
            }
        }

        private static bool IsReservedWindowsName(string segment)
        {
            string stem = segment.Split('.')[0].TrimEnd(' ', '.');
            string upper = stem.ToUpperInvariant();
            if (upper == "CON" || upper == "PRN" || upper == "AUX" || upper == "NUL")
            {
                return true;
            }
            if (upper.Length == 4 && (upper.StartsWith("COM", StringComparison.Ordinal) ||
                upper.StartsWith("LPT", StringComparison.Ordinal)))
            {
                char number = upper[3];
                return number >= '1' && number <= '9';
            }
            return false;
        }

        private static void VerifyExtractedEntryPoint(string root, ReleaseAsset package)
        {
            string executable = Path.Combine(root, package.EntryPoint);
            if (!File.Exists(executable))
            {
                throw new InvalidDataException("The package does not contain " + package.EntryPoint + ".");
            }
            string actual = Hashing.Sha256File(executable);
            if (!String.Equals(actual, package.EntryPointSha256, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidDataException("The extracted application executable failed verification.");
            }
        }

        private void PreserveLegacyMutableData()
        {
            if (!Directory.Exists(InstallRoot))
            {
                return;
            }
            string dataRoot = StandardUserDataRoot();
            string[] mutableDirectories = { "data", "exports", "logs", "backups" };
            string conflictRoot = Path.Combine(
                dataRoot,
                "migration",
                "installer-conflicts",
                DateTime.UtcNow.ToString("yyyyMMdd-HHmmss") + "-" + Guid.NewGuid().ToString("N").Substring(0, 8));
            int copied = 0;
            foreach (string name in mutableDirectories)
            {
                string source = Path.Combine(InstallRoot, name);
                if (!Directory.Exists(source))
                {
                    continue;
                }
                foreach (string sourceFile in EnumerateFilesNoFollow(source))
                {
                    string relative = sourceFile.Substring(source.Length).TrimStart(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                    string destination = Path.Combine(dataRoot, name, relative);
                    Directory.CreateDirectory(Path.GetDirectoryName(destination));
                    if (!File.Exists(destination))
                    {
                        File.Copy(sourceFile, destination, false);
                        copied++;
                    }
                    else if (!String.Equals(Hashing.Sha256File(sourceFile), Hashing.Sha256File(destination), StringComparison.OrdinalIgnoreCase))
                    {
                        string conflict = Path.Combine(conflictRoot, name, relative);
                        Directory.CreateDirectory(Path.GetDirectoryName(conflict));
                        File.Copy(sourceFile, conflict, false);
                        copied++;
                    }
                }
            }
            if (copied > 0)
            {
                Report("Preserved " + copied + " legacy data file(s) under Documents before replacement.");
            }
        }

        private static void CopyDirectory(string source, string destination)
        {
            DirectoryInfo sourceInfo = new DirectoryInfo(source);
            if (!sourceInfo.Exists)
            {
                throw new DirectoryNotFoundException(source);
            }
            if ((sourceInfo.Attributes & FileAttributes.ReparsePoint) != 0)
            {
                throw new IOException("A release directory may not be a reparse point: " + source);
            }
            Directory.CreateDirectory(destination);
            foreach (FileInfo file in sourceInfo.GetFiles())
            {
                file.CopyTo(Path.Combine(destination, file.Name), true);
            }
            foreach (DirectoryInfo child in sourceInfo.GetDirectories())
            {
                if ((child.Attributes & FileAttributes.ReparsePoint) != 0)
                {
                    throw new IOException("A release package contains a reparse point: " + child.FullName);
                }
                CopyDirectory(child.FullName, Path.Combine(destination, child.Name));
            }
        }

        private static IEnumerable<string> EnumerateFilesNoFollow(string root)
        {
            DirectoryInfo directory = new DirectoryInfo(root);
            if (!directory.Exists || (directory.Attributes & FileAttributes.ReparsePoint) != 0)
            {
                yield break;
            }
            foreach (FileInfo file in directory.GetFiles())
            {
                if ((file.Attributes & FileAttributes.ReparsePoint) == 0)
                {
                    yield return file.FullName;
                }
            }
            foreach (DirectoryInfo child in directory.GetDirectories())
            {
                if ((child.Attributes & FileAttributes.ReparsePoint) != 0)
                {
                    continue;
                }
                foreach (string file in EnumerateFilesNoFollow(child.FullName))
                {
                    yield return file;
                }
            }
        }

        private void PersistCurrentSetup()
        {
            string source = Path.GetFullPath(System.Windows.Forms.Application.ExecutablePath);
            string destination = Path.GetFullPath(MaintenanceExecutable);
            if (String.Equals(source, destination, StringComparison.OrdinalIgnoreCase))
            {
                return;
            }
            Directory.CreateDirectory(MaintenanceRoot);
            File.Copy(source, destination, true);
        }

        private void EnsureApplicationClosed()
        {
            Process[] processes = Process.GetProcessesByName(Path.GetFileNameWithoutExtension(ApplicationExeName));
            try
            {
                if (processes.Any(process => !process.HasExited))
                {
                    throw new InvalidOperationException(
                        "School CSM Control Center is running. Exit it completely from the Windows notification-area tray, then try again.");
                }
            }
            finally
            {
                foreach (Process process in processes)
                {
                    process.Dispose();
                }
            }
        }

        private void CleanupMaintenanceCache()
        {
            try
            {
                DeleteWithin(StagingRoot, MaintenanceRoot);
                DeleteWithin(RollbackRoot, MaintenanceRoot);
                DeleteFileIfPresent(InstalledManifestPath);
                DeleteFileIfPresent(InstalledPackagePath);
                string running = Path.GetFullPath(System.Windows.Forms.Application.ExecutablePath);
                string maintenance = Path.GetFullPath(MaintenanceExecutable);
                if (!String.Equals(running, maintenance, StringComparison.OrdinalIgnoreCase))
                {
                    DeleteFileIfPresent(maintenance);
                }
            }
            catch (Exception cleanupError)
            {
                Report("Some non-data maintenance files could not be removed: " + cleanupError.Message);
            }
        }

        private static void DeleteExactUserData(string dataRoot)
        {
            string documents = Path.GetFullPath(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments));
            string expected = Path.GetFullPath(Path.Combine(documents, "MoSSLab Data", DisplayName));
            string actual = Path.GetFullPath(dataRoot);
            if (!String.Equals(actual, expected, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException("Refusing to remove an unexpected user-data path.");
            }
            if (Directory.Exists(actual))
            {
                DeleteDirectoryNoFollow(actual, true);
            }
        }

        private static string StandardUserDataRoot()
        {
            string documents = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
            if (String.IsNullOrWhiteSpace(documents) || Path.GetPathRoot(documents) == documents)
            {
                throw new InvalidOperationException("Windows did not provide a safe Documents folder for this account.");
            }
            return Path.Combine(
                documents,
                "MoSSLab Data",
                DisplayName);
        }

        private static string CreateUniqueDirectory(string parent, string prefix)
        {
            Directory.CreateDirectory(parent);
            string path = Path.Combine(parent, prefix + "-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(path);
            return path;
        }

        private static void WriteBytesAtomically(string destination, byte[] bytes)
        {
            string parent = Path.GetDirectoryName(destination);
            Directory.CreateDirectory(parent);
            string temporary = destination + ".new-" + Guid.NewGuid().ToString("N");
            File.WriteAllBytes(temporary, bytes);
            if (File.Exists(destination))
            {
                File.Replace(temporary, destination, null);
            }
            else
            {
                File.Move(temporary, destination);
            }
        }

        private static long DirectorySize(string root)
        {
            long total = 0;
            foreach (string file in Directory.GetFiles(root, "*", SearchOption.AllDirectories))
            {
                total += new FileInfo(file).Length;
            }
            return total;
        }

        private static void DeleteWithin(string path, string allowedParent)
        {
            if (String.IsNullOrWhiteSpace(path) || !Directory.Exists(path))
            {
                return;
            }
            string fullPath = Path.GetFullPath(path).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string fullParent = EnsureTrailingSeparator(Path.GetFullPath(allowedParent));
            if (!fullPath.StartsWith(fullParent, StringComparison.OrdinalIgnoreCase) ||
                String.Equals(fullPath, fullParent.TrimEnd(Path.DirectorySeparatorChar), StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException("Refusing to remove a directory outside the expected maintenance boundary.");
            }
            DeleteDirectoryNoFollow(fullPath, false);
        }

        private static void DeleteDirectoryNoFollow(string root, bool rejectRootReparsePoint)
        {
            DirectoryInfo directory = new DirectoryInfo(root);
            if (!directory.Exists)
            {
                return;
            }
            if ((directory.Attributes & FileAttributes.ReparsePoint) != 0)
            {
                if (rejectRootReparsePoint)
                {
                    throw new InvalidOperationException("Refusing to follow a redirected user-data directory.");
                }
                directory.Delete(false);
                return;
            }
            foreach (FileInfo file in directory.GetFiles())
            {
                file.Attributes = FileAttributes.Normal;
                file.Delete();
            }
            foreach (DirectoryInfo child in directory.GetDirectories())
            {
                if ((child.Attributes & FileAttributes.ReparsePoint) != 0)
                {
                    child.Delete(false);
                }
                else
                {
                    DeleteDirectoryNoFollow(child.FullName, false);
                }
            }
            directory.Attributes = FileAttributes.Normal;
            directory.Delete(false);
        }

        private static void DeleteFileIfPresent(string path)
        {
            if (File.Exists(path))
            {
                File.SetAttributes(path, FileAttributes.Normal);
                File.Delete(path);
            }
        }

        private static string EnsureTrailingSeparator(string path)
        {
            return path.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
        }

        private static string ProgramFilesX86()
        {
            string path = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86);
            if (String.IsNullOrWhiteSpace(path))
            {
                path = Environment.GetEnvironmentVariable("ProgramFiles(x86)");
            }
            if (String.IsNullOrWhiteSpace(path))
            {
                throw new InvalidOperationException("Windows did not provide the Program Files (x86) location.");
            }
            return path;
        }

        private void Report(string message)
        {
            string line = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " | " + message;
            try
            {
                lock (LogLock)
                {
                    Directory.CreateDirectory(MaintenanceRoot);
                    File.AppendAllText(MaintenanceLogPath, line + Environment.NewLine);
                }
            }
            catch
            {
            }
            Action<string> handler = StatusChanged;
            if (handler != null)
            {
                handler(message);
            }
        }
    }
}

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using System.Windows.Forms;

namespace MoSSLab.SchoolCSM.Installer
{
    internal enum MaintenanceCommand
    {
        None,
        InstallOrUpdate,
        Repair,
        Rollback,
        Uninstall
    }

    internal sealed class CommandLine
    {
        internal MaintenanceCommand Command;
        internal bool RemoveUserData;
        internal bool Quiet;

        internal static CommandLine Parse(string[] arguments)
        {
            CommandLine parsed = new CommandLine();
            List<MaintenanceCommand> commands = new List<MaintenanceCommand>();
            foreach (string raw in arguments)
            {
                string argument = (raw ?? String.Empty).Trim().ToLowerInvariant();
                switch (argument)
                {
                    case "--install":
                    case "--update":
                        commands.Add(MaintenanceCommand.InstallOrUpdate);
                        break;
                    case "--repair":
                        commands.Add(MaintenanceCommand.Repair);
                        break;
                    case "--rollback":
                        commands.Add(MaintenanceCommand.Rollback);
                        break;
                    case "--uninstall":
                        commands.Add(MaintenanceCommand.Uninstall);
                        break;
                    case "--remove-user-data":
                        parsed.RemoveUserData = true;
                        break;
                    case "--quiet":
                        parsed.Quiet = true;
                        break;
                    case "--help":
                    case "-h":
                    case "/?":
                        throw new ArgumentException(
                            "Options: --install, --update, --repair, --rollback, --uninstall, " +
                            "--remove-user-data (with uninstall), --quiet");
                    default:
                        throw new ArgumentException("Unknown setup option: " + raw);
                }
            }
            if (commands.Count > 1)
            {
                throw new ArgumentException("Choose only one install, repair, rollback, or uninstall operation.");
            }
            parsed.Command = commands.Count == 0 ? MaintenanceCommand.None : commands[0];
            if (parsed.RemoveUserData && parsed.Command != MaintenanceCommand.Uninstall)
            {
                throw new ArgumentException("--remove-user-data is accepted only together with --uninstall.");
            }
            if (parsed.Quiet && parsed.Command == MaintenanceCommand.None)
            {
                throw new ArgumentException("--quiet requires an explicit maintenance operation.");
            }
            return parsed;
        }
    }

    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            CommandLine commandLine;
            try
            {
                commandLine = CommandLine.Parse(args);
            }
            catch (Exception error)
            {
                bool quietRequested = args.Any(argument =>
                    String.Equals(argument, "--quiet", StringComparison.OrdinalIgnoreCase));
                if (!quietRequested)
                {
                    MessageBox.Show(error.Message, "School CSM Control Center Setup", MessageBoxButtons.OK, MessageBoxIcon.Information);
                }
                return 2;
            }

            try
            {
                ValidateBuildConfiguration();
            }
            catch (Exception error)
            {
                if (!commandLine.Quiet)
                {
                    MessageBox.Show(error.Message, "Setup configuration error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
                return 2;
            }

            bool created;
            using (Mutex mutex = new Mutex(true, @"Global\MoSSLab.SchoolCSMControlCenter.Maintenance", out created))
            {
                if (!created)
                {
                    if (!commandLine.Quiet)
                    {
                        MessageBox.Show(
                            "Another School CSM setup or maintenance operation is already running.",
                            "School CSM Control Center Setup",
                            MessageBoxButtons.OK,
                            MessageBoxIcon.Warning);
                    }
                    return 4;
                }

                InstallerEngine engine = new InstallerEngine();
                if (commandLine.Command == MaintenanceCommand.None)
                {
                    Application.Run(new MaintenanceForm(engine));
                    return 0;
                }

                try
                {
                    RunCommand(engine, commandLine.Command, commandLine.RemoveUserData);
                    if (!commandLine.Quiet)
                    {
                        MessageBox.Show(
                            "The requested maintenance operation completed successfully.",
                            "School CSM Control Center Setup",
                            MessageBoxButtons.OK,
                            MessageBoxIcon.Information);
                    }
                    return 0;
                }
                catch (Exception error)
                {
                    if (!commandLine.Quiet)
                    {
                        MessageBox.Show(
                            error.Message + Environment.NewLine + Environment.NewLine +
                            "Details were logged to:" + Environment.NewLine + InstallerEngine.MaintenanceLogPath,
                            "Maintenance operation failed",
                            MessageBoxButtons.OK,
                            MessageBoxIcon.Error);
                    }
                    return 3;
                }
                finally
                {
                    try { mutex.ReleaseMutex(); }
                    catch (ApplicationException) { }
                }
            }
        }

        internal static void RunCommand(InstallerEngine engine, MaintenanceCommand command, bool removeUserData)
        {
            switch (command)
            {
                case MaintenanceCommand.InstallOrUpdate:
                    engine.InstallOrUpdate();
                    break;
                case MaintenanceCommand.Repair:
                    engine.Repair();
                    break;
                case MaintenanceCommand.Rollback:
                    engine.Rollback();
                    break;
                case MaintenanceCommand.Uninstall:
                    engine.Uninstall(removeUserData);
                    break;
                default:
                    throw new ArgumentOutOfRangeException("command");
            }
        }

        private static void ValidateBuildConfiguration()
        {
            string repository = BuildConfig.Repository ?? String.Empty;
            string[] pieces = repository.Split('/');
            if (pieces.Length != 2 || pieces.Any(piece => String.IsNullOrWhiteSpace(piece)) ||
                repository.IndexOf("OWNER", StringComparison.OrdinalIgnoreCase) >= 0 ||
                repository.IndexOf("REPOSITORY", StringComparison.OrdinalIgnoreCase) >= 0)
            {
                throw new InvalidDataException(
                    "This setup file has not been connected to a GitHub repository. " +
                    "The release maintainer must rebuild it with -Repository owner/repository.");
            }
            foreach (char character in repository)
            {
                if (!(Char.IsLetterOrDigit(character) || character == '/' || character == '-' ||
                    character == '_' || character == '.'))
                {
                    throw new InvalidDataException("The configured GitHub repository name is invalid.");
                }
            }
        }
    }
}

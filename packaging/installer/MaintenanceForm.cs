using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.Threading;
using System.Windows.Forms;

namespace MoSSLab.SchoolCSM.Installer
{
    internal sealed class MaintenanceForm : Form
    {
        private readonly InstallerEngine engine;
        private readonly Label installedVersion;
        private readonly Label status;
        private readonly TextBox activity;
        private readonly Button installButton;
        private readonly Button repairButton;
        private readonly Button rollbackButton;
        private readonly Button uninstallButton;
        private readonly Button launchButton;
        private readonly CheckBox removeData;
        private readonly LinkLabel releaseLink;
        private bool busy;

        internal MaintenanceForm(InstallerEngine installerEngine)
        {
            engine = installerEngine;
            engine.StatusChanged += OnStatusChanged;
            Text = "School CSM Control Center Setup";
            StartPosition = FormStartPosition.CenterScreen;
            MinimumSize = new Size(700, 590);
            Size = new Size(760, 640);
            Font = new Font("Segoe UI", 9F);
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);

            TableLayoutPanel page = new TableLayoutPanel();
            page.Dock = DockStyle.Fill;
            page.Padding = new Padding(24);
            page.ColumnCount = 1;
            page.RowCount = 9;
            page.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            page.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            page.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            page.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            page.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            page.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            page.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            page.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            page.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            Controls.Add(page);

            Label title = new Label();
            title.AutoSize = true;
            title.Font = new Font(Font.FontFamily, 18F, FontStyle.Bold);
            title.Text = "School CSM Control Center";
            title.Margin = new Padding(0, 0, 0, 4);
            page.Controls.Add(title);

            Label description = new Label();
            description.AutoSize = true;
            description.MaximumSize = new Size(680, 0);
            description.Text =
                "Install, update, repair, or remove the compiled Windows application. " +
                "Saved survey records, credentials, and the administrator-installed Internet Gateway " +
                "provider configuration remain outside Program Files and are preserved by default.";
            description.Margin = new Padding(0, 0, 0, 12);
            page.Controls.Add(description);

            installedVersion = new Label();
            installedVersion.AutoSize = true;
            installedVersion.Font = new Font(Font, FontStyle.Bold);
            installedVersion.Margin = new Padding(0, 0, 0, 12);
            page.Controls.Add(installedVersion);

            FlowLayoutPanel actions = new FlowLayoutPanel();
            actions.AutoSize = true;
            actions.Dock = DockStyle.Fill;
            actions.WrapContents = true;
            actions.Margin = new Padding(0, 0, 0, 12);
            page.Controls.Add(actions);

            installButton = MakeButton("Install / Update", InstallClicked);
            repairButton = MakeButton("Repair", RepairClicked);
            rollbackButton = MakeButton("Roll Back", RollbackClicked);
            uninstallButton = MakeButton("Uninstall", UninstallClicked);
            launchButton = MakeButton("Open Application", LaunchClicked);
            actions.Controls.Add(installButton);
            actions.Controls.Add(repairButton);
            actions.Controls.Add(rollbackButton);
            actions.Controls.Add(uninstallButton);
            actions.Controls.Add(launchButton);

            removeData = new CheckBox();
            removeData.AutoSize = true;
            removeData.Text = "When uninstalling, also remove my saved records and application credentials";
            removeData.Margin = new Padding(0, 0, 0, 12);
            page.Controls.Add(removeData);

            activity = new TextBox();
            activity.Dock = DockStyle.Fill;
            activity.Multiline = true;
            activity.ReadOnly = true;
            activity.ScrollBars = ScrollBars.Vertical;
            activity.BackColor = SystemColors.Window;
            page.Controls.Add(activity);

            status = new Label();
            status.AutoSize = true;
            status.MaximumSize = new Size(680, 0);
            status.Text = "Ready.";
            status.Margin = new Padding(0, 12, 0, 8);
            page.Controls.Add(status);

            releaseLink = new LinkLabel();
            releaseLink.AutoSize = true;
            releaseLink.Text = "View release source on GitHub";
            releaseLink.LinkClicked += delegate
            {
                Process.Start(new ProcessStartInfo
                {
                    FileName = "https://github.com/" + BuildConfig.Repository + "/releases",
                    UseShellExecute = true
                });
            };
            page.Controls.Add(releaseLink);

            Label privacy = new Label();
            privacy.AutoSize = true;
            privacy.ForeColor = SystemColors.GrayText;
            privacy.MaximumSize = new Size(680, 0);
            privacy.Text =
                "The explicit removal option affects only the current Windows account's standard Documents data folder. " +
                "It is never selected automatically. Deployment provider configuration is retained in ProgramData.";
            privacy.Margin = new Padding(0, 8, 0, 0);
            page.Controls.Add(privacy);

            RefreshState();
        }

        protected override void OnFormClosed(FormClosedEventArgs e)
        {
            engine.StatusChanged -= OnStatusChanged;
            base.OnFormClosed(e);
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (busy && e.CloseReason == CloseReason.UserClosing)
            {
                e.Cancel = true;
                MessageBox.Show(
                    "Wait for the current maintenance operation to finish before closing setup.",
                    Text,
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Information);
                return;
            }
            base.OnFormClosing(e);
        }

        private Button MakeButton(string text, EventHandler handler)
        {
            Button button = new Button();
            button.AutoSize = true;
            button.MinimumSize = new Size(112, 36);
            button.Text = text;
            button.Click += handler;
            return button;
        }

        private void RefreshState()
        {
            bool installed = engine.IsInstalled;
            installedVersion.Text = installed
                ? "Installed version: " + engine.InstalledVersion
                : "The application is not currently installed.";
            installButton.Text = installed ? "Check for Update" : "Install";
            repairButton.Enabled = installed && !busy;
            rollbackButton.Enabled = installed && engine.CanRollback && !busy;
            uninstallButton.Enabled = installed && !busy;
            launchButton.Enabled = installed && !busy;
            installButton.Enabled = !busy;
            removeData.Enabled = installed && !busy;
            releaseLink.Enabled = !busy;
        }

        private void InstallClicked(object sender, EventArgs e)
        {
            StartOperation(MaintenanceCommand.InstallOrUpdate, false);
        }

        private void RepairClicked(object sender, EventArgs e)
        {
            DialogResult answer = MessageBox.Show(
                "Repair will download and reinstall the same verified release. Saved records will not be changed. Continue?",
                "Repair application",
                MessageBoxButtons.YesNo,
                MessageBoxIcon.Question);
            if (answer == DialogResult.Yes)
            {
                StartOperation(MaintenanceCommand.Repair, false);
            }
        }

        private void RollbackClicked(object sender, EventArgs e)
        {
            DialogResult answer = MessageBox.Show(
                "Restore the previous verified application files? Saved records will not be changed.",
                "Roll back application",
                MessageBoxButtons.YesNo,
                MessageBoxIcon.Question);
            if (answer == DialogResult.Yes)
            {
                StartOperation(MaintenanceCommand.Rollback, false);
            }
        }

        private void UninstallClicked(object sender, EventArgs e)
        {
            string dataText = removeData.Checked
                ? "Your saved records, optional OpenAI key, and Internet Gateway device credentials for this Windows account will also be permanently removed."
                : "Your saved records and credentials will be preserved.";
            dataText += Environment.NewLine + Environment.NewLine +
                "The administrator-installed Internet Gateway provider configuration in ProgramData will be preserved.";
            DialogResult answer = MessageBox.Show(
                "Remove School CSM Control Center from Program Files?" + Environment.NewLine + Environment.NewLine + dataText,
                "Confirm uninstall",
                MessageBoxButtons.YesNo,
                removeData.Checked ? MessageBoxIcon.Warning : MessageBoxIcon.Question);
            if (answer == DialogResult.Yes)
            {
                StartOperation(MaintenanceCommand.Uninstall, removeData.Checked);
            }
        }

        private void LaunchClicked(object sender, EventArgs e)
        {
            try
            {
                engine.LaunchApplication();
                Close();
            }
            catch (Exception error)
            {
                MessageBox.Show(error.Message, Text, MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        private void StartOperation(MaintenanceCommand command, bool removeUserData)
        {
            if (busy)
            {
                return;
            }
            busy = true;
            activity.Clear();
            status.Text = "Working...";
            RefreshState();

            BackgroundWorker worker = new BackgroundWorker();
            worker.DoWork += delegate
            {
                Program.RunCommand(engine, command, removeUserData);
            };
            worker.RunWorkerCompleted += delegate(object completedSender, RunWorkerCompletedEventArgs completed)
            {
                busy = false;
                RefreshState();
                if (completed.Error != null)
                {
                    engine.RecordFailure(completed.Error);
                    status.Text = "The operation did not complete.";
                    MessageBox.Show(
                        completed.Error.Message + Environment.NewLine + Environment.NewLine +
                        "Details were logged to:" + Environment.NewLine + InstallerEngine.MaintenanceLogPath,
                        "Maintenance operation failed",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Error);
                }
                else
                {
                    status.Text = "Operation completed successfully.";
                    MessageBox.Show(status.Text, Text, MessageBoxButtons.OK, MessageBoxIcon.Information);
                }
                worker.Dispose();
            };
            worker.RunWorkerAsync();
        }

        private void OnStatusChanged(string message)
        {
            if (IsDisposed)
            {
                return;
            }
            if (InvokeRequired)
            {
                BeginInvoke(new Action<string>(OnStatusChanged), message);
                return;
            }
            status.Text = message;
            activity.AppendText(DateTime.Now.ToString("HH:mm:ss") + "  " + message + Environment.NewLine);
        }
    }
}

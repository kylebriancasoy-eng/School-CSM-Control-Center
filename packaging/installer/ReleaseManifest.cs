using System;
using System.IO;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Text;

namespace MoSSLab.SchoolCSM.Installer
{
    [DataContract]
    internal sealed class ReleaseManifest
    {
        [DataMember(Name = "schemaVersion")]
        internal int SchemaVersion;

        [DataMember(Name = "applicationId")]
        internal string ApplicationId;

        [DataMember(Name = "version")]
        internal string VersionText;

        [DataMember(Name = "windowsVersion")]
        internal string WindowsVersion;

        [DataMember(Name = "tag")]
        internal string Tag;

        [DataMember(Name = "publishedAt")]
        internal string PublishedAt;

        [DataMember(Name = "releasePage")]
        internal string ReleasePage;

        [DataMember(Name = "package")]
        internal ReleaseAsset Package;

        [DataMember(Name = "installer")]
        internal ReleaseAsset Installer;

        internal Version ParsedVersion
        {
            get
            {
                Version parsed;
                if (!System.Version.TryParse(VersionText, out parsed))
                {
                    throw new InvalidDataException("The release version is invalid.");
                }
                return parsed;
            }
        }

        internal void Validate(string repository)
        {
            if (SchemaVersion != 1)
            {
                throw new InvalidDataException("This setup program cannot read release schema " + SchemaVersion + ".");
            }
            if (!String.Equals(ApplicationId, InstallerEngine.ApplicationId, StringComparison.Ordinal))
            {
                throw new InvalidDataException("The release is intended for a different application.");
            }
            string[] versionParts = (VersionText ?? String.Empty).Split('.');
            if (versionParts.Length != 3 || String.IsNullOrWhiteSpace(Tag) ||
                !String.Equals(Tag, "v" + ParsedVersion, StringComparison.Ordinal))
            {
                throw new InvalidDataException("The release tag and version do not agree.");
            }
            Version windows;
            string[] windowsParts = (WindowsVersion ?? String.Empty).Split('.');
            if (windowsParts.Length != 4 || !System.Version.TryParse(WindowsVersion, out windows) ||
                windows.Major != ParsedVersion.Major || windows.Minor != ParsedVersion.Minor ||
                windows.Build != ParsedVersion.Build)
            {
                throw new InvalidDataException("The Windows file version and release version do not agree.");
            }
            DateTimeOffset published;
            if (!DateTimeOffset.TryParse(PublishedAt, out published))
            {
                throw new InvalidDataException("The release publication date is invalid.");
            }
            Uri releaseUri;
            string expectedPage = "/" + repository + "/releases/tag/" + Tag;
            if (!Uri.TryCreate(ReleasePage, UriKind.Absolute, out releaseUri) ||
                releaseUri.Scheme != Uri.UriSchemeHttps ||
                !String.Equals(releaseUri.Host, "github.com", StringComparison.OrdinalIgnoreCase) ||
                !String.Equals(releaseUri.AbsolutePath, expectedPage, StringComparison.OrdinalIgnoreCase) ||
                !releaseUri.IsDefaultPort || !String.IsNullOrEmpty(releaseUri.UserInfo) ||
                !String.IsNullOrEmpty(releaseUri.Query) || !String.IsNullOrEmpty(releaseUri.Fragment))
            {
                throw new InvalidDataException("The release page does not match the configured GitHub repository and tag.");
            }
            if (Package == null)
            {
                throw new InvalidDataException("The release does not identify an application package.");
            }
            Package.Validate(repository, Tag, true);
            if (Installer == null)
            {
                throw new InvalidDataException("The release does not identify its setup program.");
            }
            Installer.Validate(repository, Tag, false);
        }

        internal static ReleaseManifest Read(byte[] bytes)
        {
            if (bytes == null || bytes.Length == 0 || bytes.Length > 1024 * 1024)
            {
                throw new InvalidDataException("The release manifest is empty or unexpectedly large.");
            }
            DataContractJsonSerializer serializer = new DataContractJsonSerializer(typeof(ReleaseManifest));
            using (MemoryStream stream = new MemoryStream(bytes, false))
            {
                ReleaseManifest manifest = serializer.ReadObject(stream) as ReleaseManifest;
                if (manifest == null)
                {
                    throw new InvalidDataException("The release manifest could not be read.");
                }
                return manifest;
            }
        }

        internal byte[] ToBytes()
        {
            DataContractJsonSerializer serializer = new DataContractJsonSerializer(typeof(ReleaseManifest));
            using (MemoryStream stream = new MemoryStream())
            {
                serializer.WriteObject(stream, this);
                return stream.ToArray();
            }
        }
    }

    [DataContract]
    internal sealed class ReleaseAsset
    {
        [DataMember(Name = "fileName")]
        internal string FileName;

        [DataMember(Name = "url")]
        internal string Url;

        [DataMember(Name = "sha256")]
        internal string Sha256;

        [DataMember(Name = "sizeBytes")]
        internal long SizeBytes;

        [DataMember(Name = "format")]
        internal string Format;

        [DataMember(Name = "entryPoint")]
        internal string EntryPoint;

        [DataMember(Name = "entryPointSha256")]
        internal string EntryPointSha256;

        internal void Validate(string repository, string tag, bool package)
        {
            if (String.IsNullOrWhiteSpace(FileName) || !String.Equals(Path.GetFileName(FileName), FileName, StringComparison.Ordinal))
            {
                throw new InvalidDataException("A release asset has an unsafe file name.");
            }
            Uri uri;
            if (!Uri.TryCreate(Url, UriKind.Absolute, out uri) || uri.Scheme != Uri.UriSchemeHttps)
            {
                throw new InvalidDataException("A release download address is invalid or is not HTTPS.");
            }
            string expectedPath = "/" + repository + "/releases/download/" + tag + "/" + Uri.EscapeDataString(FileName);
            if (!String.Equals(uri.Host, "github.com", StringComparison.OrdinalIgnoreCase) ||
                !String.Equals(uri.AbsolutePath, expectedPath, StringComparison.OrdinalIgnoreCase) ||
                !uri.IsDefaultPort || !String.IsNullOrEmpty(uri.UserInfo) ||
                !String.IsNullOrEmpty(uri.Query) || !String.IsNullOrEmpty(uri.Fragment))
            {
                throw new InvalidDataException("A release asset does not belong to the configured GitHub repository.");
            }
            if (!Hashing.IsSha256(Sha256) || SizeBytes <= 0)
            {
                throw new InvalidDataException("A release asset is missing valid checksum metadata.");
            }
            if (package)
            {
                if (!String.Equals(Format, "zip", StringComparison.OrdinalIgnoreCase))
                {
                    throw new InvalidDataException("The application package is not a supported ZIP archive.");
                }
                if (!String.Equals(EntryPoint, InstallerEngine.ApplicationExeName, StringComparison.Ordinal) ||
                    !Hashing.IsSha256(EntryPointSha256))
                {
                    throw new InvalidDataException("The package entry point metadata is invalid.");
                }
            }
            else if (!String.Equals(FileName, InstallerEngine.SetupExeName, StringComparison.Ordinal))
            {
                throw new InvalidDataException("The setup-program metadata has an unexpected file name.");
            }
        }
    }

    internal static class Hashing
    {
        internal static bool IsSha256(string value)
        {
            if (String.IsNullOrEmpty(value) || value.Length != 64)
            {
                return false;
            }
            for (int index = 0; index < value.Length; index++)
            {
                char character = value[index];
                bool valid = (character >= '0' && character <= '9') ||
                    (character >= 'a' && character <= 'f') ||
                    (character >= 'A' && character <= 'F');
                if (!valid)
                {
                    return false;
                }
            }
            return true;
        }

        internal static string Sha256File(string path)
        {
            using (FileStream stream = File.OpenRead(path))
            using (System.Security.Cryptography.SHA256 algorithm = System.Security.Cryptography.SHA256.Create())
            {
                return BitConverter.ToString(algorithm.ComputeHash(stream)).Replace("-", String.Empty);
            }
        }
    }
}

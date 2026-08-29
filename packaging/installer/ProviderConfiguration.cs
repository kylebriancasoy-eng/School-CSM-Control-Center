using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Text.RegularExpressions;

namespace MoSSLab.SchoolCSM.Installer
{
    [DataContract]
    internal sealed class ProviderConfigurationDocument
    {
        [DataMember(Name = "schema_version")]
        internal string SchemaVersion;

        [DataMember(Name = "registration_service_url")]
        internal string RegistrationServiceUrl;

        [DataMember(Name = "managed_domain")]
        internal string ManagedDomain;

        [DataMember(Name = "authorization_signing_keys")]
        internal ProviderSigningKey[] AuthorizationSigningKeys;

        [DataMember(Name = "trusted_proxy_addresses", EmitDefaultValue = false)]
        internal string[] TrustedProxyAddresses;
    }

    [DataContract]
    internal sealed class ProviderSigningKey
    {
        [DataMember(Name = "key_id")]
        internal string KeyId;

        [DataMember(Name = "public_key_base64")]
        internal string PublicKeyBase64;
    }

    internal static class ProviderConfiguration
    {
        private const int MaximumBytes = 256 * 1024;
        private static readonly Regex DomainPattern = new Regex(
            @"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\.?$",
            RegexOptions.CultureInvariant | RegexOptions.IgnoreCase);
        private static readonly Regex KeyIdPattern = new Regex(
            @"^[A-Za-z0-9._-]{1,80}$",
            RegexOptions.CultureInvariant);
        private static readonly Regex Ed25519Base64Pattern = new Regex(
            @"^[A-Za-z0-9+/]{43}=$",
            RegexOptions.CultureInvariant);

        internal static byte[] ReadValidated(string path)
        {
            FileInfo file = new FileInfo(path);
            if (!file.Exists || (file.Attributes & FileAttributes.ReparsePoint) != 0)
            {
                throw new InvalidDataException(
                    "The Internet Gateway provider configuration is missing or redirected.");
            }
            if (file.Length <= 0 || file.Length > MaximumBytes)
            {
                throw new InvalidDataException(
                    "The Internet Gateway provider configuration has an unsupported size.");
            }

            byte[] exactBytes = File.ReadAllBytes(path);
            ProviderConfigurationDocument document;
            try
            {
                DataContractJsonSerializer serializer = new DataContractJsonSerializer(
                    typeof(ProviderConfigurationDocument));
                using (MemoryStream stream = new MemoryStream(exactBytes, false))
                {
                    document = serializer.ReadObject(stream) as ProviderConfigurationDocument;
                }
            }
            catch (Exception error)
            {
                if (error is OutOfMemoryException)
                {
                    throw;
                }
                throw new InvalidDataException(
                    "The Internet Gateway provider configuration is not valid JSON.", error);
            }
            Validate(document);
            return exactBytes;
        }

        private static void Validate(ProviderConfigurationDocument document)
        {
            if (document == null || !String.Equals(document.SchemaVersion, "1.0", StringComparison.Ordinal))
            {
                throw new InvalidDataException(
                    "The Internet Gateway provider configuration version is unsupported.");
            }

            Uri serviceUri;
            string serviceUrl = (document.RegistrationServiceUrl ?? String.Empty).Trim();
            if (!Uri.TryCreate(serviceUrl, UriKind.Absolute, out serviceUri) ||
                !String.Equals(serviceUri.Scheme, Uri.UriSchemeHttps, StringComparison.OrdinalIgnoreCase) ||
                String.IsNullOrWhiteSpace(serviceUri.Host) ||
                !String.IsNullOrEmpty(serviceUri.UserInfo) ||
                !String.IsNullOrEmpty(serviceUri.Query) ||
                !String.IsNullOrEmpty(serviceUri.Fragment))
            {
                throw new InvalidDataException(
                    "The Internet Gateway registration service address is invalid.");
            }

            string domain = (document.ManagedDomain ?? String.Empty).Trim();
            if (!DomainPattern.IsMatch(domain))
            {
                throw new InvalidDataException(
                    "The Internet Gateway managed domain is invalid.");
            }

            ProviderSigningKey[] keys = document.AuthorizationSigningKeys;
            if (keys == null || keys.Length == 0)
            {
                throw new InvalidDataException(
                    "The Internet Gateway provider configuration has no signing keys.");
            }
            HashSet<string> keyIds = new HashSet<string>(StringComparer.Ordinal);
            foreach (ProviderSigningKey key in keys)
            {
                string keyId = key == null ? String.Empty : (key.KeyId ?? String.Empty).Trim();
                string material = key == null ? String.Empty : (key.PublicKeyBase64 ?? String.Empty);
                byte[] decoded;
                if (!KeyIdPattern.IsMatch(keyId) || !keyIds.Add(keyId) ||
                    !Ed25519Base64Pattern.IsMatch(material))
                {
                    throw new InvalidDataException(
                        "The Internet Gateway provider configuration has an invalid signing key.");
                }
                try
                {
                    decoded = Convert.FromBase64String(material);
                }
                catch (FormatException error)
                {
                    throw new InvalidDataException(
                        "The Internet Gateway provider configuration has an invalid signing key.", error);
                }
                if (decoded.Length != 32)
                {
                    throw new InvalidDataException(
                        "The Internet Gateway signing keys must be Ed25519 public keys.");
                }
            }

            string[] proxies = document.TrustedProxyAddresses;
            if (proxies == null || proxies.Length == 0)
            {
                throw new InvalidDataException(
                    "The Internet Gateway provider configuration has no trusted proxy addresses.");
            }
            foreach (string rawProxy in proxies)
            {
                string proxy = (rawProxy ?? String.Empty).Trim();
                IPAddress parsed;
                if ((!proxy.Contains(":") && proxy.Split('.').Length != 4) ||
                    !IPAddress.TryParse(proxy, out parsed))
                {
                    throw new InvalidDataException(
                        "The Internet Gateway provider configuration has an invalid trusted proxy address.");
                }
            }
        }
    }
}

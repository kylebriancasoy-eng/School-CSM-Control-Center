using System;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Threading;

namespace MoSSLab.SchoolCSM.Installer
{
    internal sealed class ResilientDownloader
    {
        internal const int MaximumAttempts = 5;
        internal const int RequestTimeoutMilliseconds = 45000;
        internal const int ReadWriteTimeoutMilliseconds = 60000;

        private readonly Action<string> report;

        internal ResilientDownloader(Action<string> statusReporter)
        {
            report = statusReporter;
        }

        internal byte[] DownloadBytes(string url, long maximumBytes, string description)
        {
            for (int attempt = 1; attempt <= MaximumAttempts; attempt++)
            {
                try
                {
                    using (HttpWebResponse response = OpenResponse(url, 0))
                    {
                        RequireStatus(response, HttpStatusCode.OK, description);
                        if (response.ContentLength > maximumBytes)
                        {
                            throw new InvalidDataException(description + " is unexpectedly large.");
                        }
                        using (Stream input = response.GetResponseStream())
                        using (MemoryStream output = new MemoryStream())
                        {
                            CopyWithLimit(
                                input,
                                output,
                                maximumBytes,
                                description + " is unexpectedly large.");
                            if (response.ContentLength >= 0 && output.Length != response.ContentLength)
                            {
                                throw new IncompleteDownloadException(
                                    "The server closed the connection before " + description + " finished downloading.");
                            }
                            if (output.Length == 0)
                            {
                                throw new InvalidDataException(description + " is empty.");
                            }
                            return output.ToArray();
                        }
                    }
                }
                catch (Exception error)
                {
                    if (!RetryOrThrow(error, attempt, description))
                    {
                        throw;
                    }
                }
            }
            throw new InvalidOperationException("The download retry loop ended unexpectedly.");
        }

        internal void DownloadFile(string url, string destination, long expectedBytes, string description)
        {
            if (expectedBytes <= 0)
            {
                throw new InvalidDataException(description + " has an invalid expected size.");
            }

            for (int attempt = 1; attempt <= MaximumAttempts; attempt++)
            {
                long existingBytes = File.Exists(destination) ? new FileInfo(destination).Length : 0;
                if (existingBytes > expectedBytes)
                {
                    throw new InvalidDataException(description + " is larger than the release manifest declares.");
                }
                if (existingBytes == expectedBytes)
                {
                    return;
                }

                try
                {
                    using (HttpWebResponse response = OpenResponse(url, existingBytes))
                    {
                        long writeOffset = ValidateFileResponse(response, existingBytes, expectedBytes, description);
                        long remaining = expectedBytes - writeOffset;
                        if (response.ContentLength > remaining)
                        {
                            throw new InvalidDataException(
                                description + " is larger than the release manifest declares.");
                        }

                        using (Stream input = response.GetResponseStream())
                        using (FileStream output = new FileStream(
                            destination,
                            FileMode.OpenOrCreate,
                            FileAccess.Write,
                            FileShare.None))
                        {
                            if (writeOffset == 0)
                            {
                                output.SetLength(0);
                            }
                            else if (output.Length != writeOffset)
                            {
                                throw new IOException("The partial download changed unexpectedly.");
                            }
                            output.Position = writeOffset;
                            CopyWithLimit(
                                input,
                                output,
                                remaining,
                                description + " is larger than the release manifest declares.");
                            output.Flush();
                        }
                    }

                    long downloadedBytes = new FileInfo(destination).Length;
                    if (downloadedBytes < expectedBytes)
                    {
                        throw new IncompleteDownloadException(
                            "The server closed the connection before " + description + " finished downloading.");
                    }
                    if (downloadedBytes > expectedBytes)
                    {
                        throw new InvalidDataException(
                            description + " is larger than the release manifest declares.");
                    }
                    return;
                }
                catch (Exception error)
                {
                    if (!RetryOrThrow(error, attempt, description))
                    {
                        throw;
                    }
                }
            }
            throw new InvalidOperationException("The download retry loop ended unexpectedly.");
        }

        private long ValidateFileResponse(
            HttpWebResponse response,
            long existingBytes,
            long expectedBytes,
            string description)
        {
            if (response.StatusCode == HttpStatusCode.OK)
            {
                if (existingBytes > 0 && report != null)
                {
                    report("The server could not continue the partial download; restarting it safely.");
                }
                return 0;
            }
            if (response.StatusCode != HttpStatusCode.PartialContent)
            {
                throw new InvalidDataException(
                    "The server returned an unexpected response while downloading " + description + ".");
            }

            string contentRange = response.Headers[HttpResponseHeader.ContentRange];
            long rangeStart;
            long rangeEnd;
            long rangeTotal;
            if (!TryParseContentRange(contentRange, out rangeStart, out rangeEnd, out rangeTotal) ||
                rangeStart != existingBytes || rangeEnd < rangeStart ||
                rangeEnd >= expectedBytes || rangeTotal != expectedBytes)
            {
                throw new InvalidDataException(
                    "The server returned an invalid partial-download range for " + description + ".");
            }
            if (response.ContentLength >= 0 && response.ContentLength != rangeEnd - rangeStart + 1)
            {
                throw new InvalidDataException(
                    "The server returned an inconsistent partial-download length for " + description + ".");
            }
            return existingBytes;
        }

        internal static bool TryParseContentRange(
            string value,
            out long start,
            out long end,
            out long total)
        {
            start = 0;
            end = 0;
            total = 0;
            if (String.IsNullOrWhiteSpace(value) ||
                !value.StartsWith("bytes ", StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }
            string range = value.Substring(6);
            int dash = range.IndexOf('-');
            int slash = range.IndexOf('/');
            if (dash <= 0 || slash <= dash + 1 || slash >= range.Length - 1)
            {
                return false;
            }
            return Int64.TryParse(
                       range.Substring(0, dash),
                       NumberStyles.None,
                       CultureInfo.InvariantCulture,
                       out start) &&
                   Int64.TryParse(
                       range.Substring(dash + 1, slash - dash - 1),
                       NumberStyles.None,
                       CultureInfo.InvariantCulture,
                       out end) &&
                   Int64.TryParse(
                       range.Substring(slash + 1),
                       NumberStyles.None,
                       CultureInfo.InvariantCulture,
                       out total);
        }

        private HttpWebResponse OpenResponse(string url, long rangeStart)
        {
            ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12;
            HttpWebRequest request = WebRequest.CreateHttp(url);
            request.Method = "GET";
            request.AllowAutoRedirect = true;
            request.MaximumAutomaticRedirections = 10;
            request.KeepAlive = false;
            request.Pipelined = false;
            request.Timeout = RequestTimeoutMilliseconds;
            request.ReadWriteTimeout = ReadWriteTimeoutMilliseconds;
            request.UserAgent = "MoSSLab-School-CSM-Installer/" + BuildConfig.InstallerVersion;
            request.Accept = "application/octet-stream, application/json";
            if (request.Proxy != null)
            {
                request.Proxy.Credentials = CredentialCache.DefaultCredentials;
            }
            if (rangeStart > 0)
            {
                request.AddRange(rangeStart);
            }
            return (HttpWebResponse)request.GetResponse();
        }

        private bool RetryOrThrow(Exception error, int attempt, string description)
        {
            if (!IsTransient(error))
            {
                return false;
            }
            if (attempt >= MaximumAttempts)
            {
                if (report != null)
                {
                    report(
                        "GitHub download failed after " + MaximumAttempts + " attempts: " +
                        SafeMessage(error));
                }
                throw new IOException(
                    "Setup could not download " + description + " after " + MaximumAttempts +
                    " attempts. Check the internet connection, proxy, or security software, then try again.",
                    error);
            }

            int delaySeconds = 1 << (attempt - 1);
            if (report != null)
            {
                report(
                    "The GitHub connection was interrupted (" + SafeMessage(error) + "). " +
                    "Retrying " + description + " (attempt " + (attempt + 1) + " of " +
                    MaximumAttempts + ") in " + delaySeconds + " second" +
                    (delaySeconds == 1 ? "." : "s."));
            }
            Thread.Sleep(delaySeconds * 1000);
            return true;
        }

        internal static bool IsTransient(Exception error)
        {
            for (Exception current = error; current != null; current = current.InnerException)
            {
                if (current is IncompleteDownloadException || current is SocketException)
                {
                    return true;
                }
                WebException web = current as WebException;
                if (web == null)
                {
                    continue;
                }
                if (web.Status == WebExceptionStatus.ProtocolError)
                {
                    HttpWebResponse response = web.Response as HttpWebResponse;
                    if (response == null)
                    {
                        return false;
                    }
                    try
                    {
                        int status = (int)response.StatusCode;
                        return status == 408 || status == 429 || status == 500 ||
                            status == 502 || status == 503 || status == 504;
                    }
                    finally
                    {
                        response.Close();
                    }
                }
                switch (web.Status)
                {
                    case WebExceptionStatus.ConnectionClosed:
                    case WebExceptionStatus.ConnectFailure:
                    case WebExceptionStatus.KeepAliveFailure:
                    case WebExceptionStatus.NameResolutionFailure:
                    case WebExceptionStatus.PipelineFailure:
                    case WebExceptionStatus.ProxyNameResolutionFailure:
                    case WebExceptionStatus.ReceiveFailure:
                    case WebExceptionStatus.RequestCanceled:
                    case WebExceptionStatus.SecureChannelFailure:
                    case WebExceptionStatus.SendFailure:
                    case WebExceptionStatus.Timeout:
                        return true;
                    default:
                        return false;
                }
            }
            return false;
        }

        private static string SafeMessage(Exception error)
        {
            string message = error == null ? String.Empty : error.Message;
            if (String.IsNullOrWhiteSpace(message))
            {
                return "temporary network error";
            }
            message = message.Replace('\r', ' ').Replace('\n', ' ').Trim();
            return message.Length <= 180 ? message : message.Substring(0, 180) + "...";
        }

        private static void RequireStatus(
            HttpWebResponse response,
            HttpStatusCode required,
            string description)
        {
            if (response.StatusCode != required)
            {
                throw new InvalidDataException(
                    "The server returned an unexpected response while downloading " + description + ".");
            }
        }

        private static void CopyWithLimit(
            Stream input,
            Stream output,
            long maximumBytes,
            string errorMessage)
        {
            if (input == null)
            {
                throw new IOException("The download server returned no data.");
            }
            byte[] buffer = new byte[1024 * 1024];
            long total = 0;
            int read;
            while ((read = input.Read(buffer, 0, buffer.Length)) > 0)
            {
                if (total > maximumBytes - read)
                {
                    throw new InvalidDataException(errorMessage);
                }
                output.Write(buffer, 0, read);
                total += read;
            }
        }

        private sealed class IncompleteDownloadException : IOException
        {
            internal IncompleteDownloadException(string message)
                : base(message)
            {
            }
        }
    }
}

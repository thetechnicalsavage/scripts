// v1.0 - 2026-11-11 - Lab client: one HTTPS GET over a socket from the JVM default SSLSocketFactory.
//
// This is the same TLS path as libraries that open their own sockets with SSLSocketFactory.getDefault()
// (for example Axis2's CommonsHTTPTransportSender on commons-httpclient 3). Their trust comes from the JVM
// default trust store, never from WebLogic's keystore configuration.
//
// Usage: java CallBackend https://host:port/path
// Exit:  0 = HTTP status line read, 1 = TLS/IO failure, 2 = usage error.

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import javax.net.ssl.SSLParameters;
import javax.net.ssl.SSLSocket;
import javax.net.ssl.SSLSocketFactory;

public class CallBackend {
    public static void main(String[] args) {
        if (args.length != 1) {
            System.err.println("usage: java CallBackend https://host:port/path");
            System.exit(2);
        }
        URI uri = URI.create(args[0]);
        String host = uri.getHost();
        int port = uri.getPort() == -1 ? 443 : uri.getPort();
        String path = (uri.getRawPath() == null || uri.getRawPath().isEmpty()) ? "/" : uri.getRawPath();

        Socket plain = new Socket();
        try {
            plain.connect(new InetSocketAddress(host, port), 5000);
            plain.setSoTimeout(5000);
            // JVM default factory: trust = javax.net.ssl.trustStore, else jssecacerts, else cacerts.
            SSLSocket s = (SSLSocket) ((SSLSocketFactory) SSLSocketFactory.getDefault())
                    .createSocket(plain, host, port, true);
            SSLParameters p = s.getSSLParameters();
            p.setEndpointIdentificationAlgorithm("HTTPS");   // check the host name, like a real HTTPS client
            s.setSSLParameters(p);
            s.startHandshake();

            OutputStream out = s.getOutputStream();
            out.write(("GET " + path + " HTTP/1.0\r\nHost: " + host + "\r\n\r\n").getBytes(StandardCharsets.US_ASCII));
            out.flush();
            BufferedReader in = new BufferedReader(new InputStreamReader(s.getInputStream(), StandardCharsets.US_ASCII));
            System.out.println(in.readLine());   // HTTP status line
            s.close();
        } catch (IOException e) {
            System.out.println(e);                // class name + message, as in the server log
            System.exit(1);
        } finally {
            try { plain.close(); } catch (IOException ignore) { /* already failing or done */ }
        }
    }
}

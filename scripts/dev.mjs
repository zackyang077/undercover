// All three child processes inherit the development environment on macOS and Windows.
import concurrently from "concurrently";

const services = ["backend", "dashboard", "agent"];
const { result } = concurrently(
  services.map((name) => ({ name, command: "npm:dev:" + name })),
  { killOthersOn: ["success", "failure"] },
);
try {
  await result;
} catch {
  process.exitCode = 1;
}

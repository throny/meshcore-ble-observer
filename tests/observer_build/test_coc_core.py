"""Compile and execute the allocation-free CoC core; never contact Bluetooth."""
import os
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class CocCoreTests(unittest.TestCase):
    def compile_and_run(self, name, extra=()):
        compiler = shutil.which("g++")
        if not compiler:
            self.skipTest("native C++ compiler unavailable")
        with tempfile.TemporaryDirectory(prefix="observer-coc-") as directory:
            output = Path(directory) / name
            command = [compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                       "-Wno-error=reorder", "-I", str(ROOT / "src"), *extra,
                       str(ROOT / "test/test_observer_coc" / (name + ".cpp")),
                       "-o", str(output)]
            built = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("PASS", ran.stdout)

    def test_protocol(self):
        self.compile_and_run("test_protocol")

    def test_real_event_server(self):
        source = os.environ.get("OBSERVER_COC_FRAMEWORK_SOURCE")
        cmsis = os.environ.get("OBSERVER_COC_CMSIS_INCLUDE")
        if source is None or cmsis is None:
            self.skipTest("set SDK and CMSIS includes for real Nordic event structures")
        include = Path(source) / "cores/nRF5/nordic/softdevice/s140_nrf52_7.3.0_API/include"
        self.assertTrue(include.is_dir())
        self.compile_and_run("test_server", ["-DSVCALL_AS_NORMAL_FUNCTION", "-DNRF52840_XXAA",
            "-isystem", str(include), "-isystem", str(Path(source) / "cores/nRF5/nordic/nrfx/mdk"),
            "-isystem", cmsis])

    def test_real_nordic_port(self):
        source = os.environ.get("OBSERVER_COC_FRAMEWORK_SOURCE")
        cmsis = os.environ.get("OBSERVER_COC_CMSIS_INCLUDE")
        if source is None or cmsis is None:
            self.skipTest("set SDK and CMSIS includes for real Nordic port headers")
        include = Path(source) / "cores/nRF5/nordic/softdevice/s140_nrf52_7.3.0_API/include"
        self.assertTrue(include.is_dir())
        self.compile_and_run("test_nordic_port", ["-DSVCALL_AS_NORMAL_FUNCTION", "-DNRF52840_XXAA",
            "-isystem", str(include), "-isystem", str(Path(source) / "cores/nRF5/nordic/nrfx/mdk"),
            "-isystem", cmsis])

    def test_delivery_selected_queue(self):
        source = os.environ.get("OBSERVER_COC_QUEUE_SOURCE")
        if source is None:
            self.skipTest("set OBSERVER_COC_QUEUE_SOURCE for exact selected 544-byte header")
        header = Path(source).resolve()
        self.assertTrue(header.is_file(), "explicit queue fixture does not exist")
        self.compile_and_run("test_delivery", [
            '-DCOC_QUEUE_INCLUDE="' + str(header) + '"', "-DCOC_QUEUE_LIMIT=544"])

    def test_framework_patch_matches_pinned_source(self):
        source = os.environ.get("OBSERVER_COC_FRAMEWORK_SOURCE")
        if source is None:
            self.skipTest("set OBSERVER_COC_FRAMEWORK_SOURCE for pinned framework patch check")
        original = Path(source) / "libraries/Bluefruit52Lib/src/bluefruit.cpp"
        self.assertTrue(original.is_file())
        self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(),
                         "e0948ab89ce44748e26d5fd950caa9bafb00da78e16fcc6e77f84a38cfd798f6")
        self.assertNotIn("bluefruit_configure_peripheral", original.read_text())
        patch = ROOT / "firmware/observer-coc-experimental/bluefruit-pre-enable.patch"
        self.assertTrue(patch.is_file(), "optional pre-enable framework patch missing")
        with tempfile.TemporaryDirectory(prefix="observer-coc-framework-") as directory:
            target = Path(directory) / "libraries/Bluefruit52Lib/src/bluefruit.cpp"
            target.parent.mkdir(parents=True)
            shutil.copyfile(original, target)
            applied = subprocess.run(["git", "apply", str(patch)], cwd=directory,
                                     capture_output=True, text=True, timeout=10)
            self.assertEqual(applied.returncode, 0, applied.stderr)
            text = target.read_text()
            call = "bluefruit_configure_peripheral(CONN_CFG_PERIPHERAL, ram_start)"
            self.assertIn(call, text)
            self.assertLess(text.index(call), text.index("sd_ble_enable(&ram_start)"))
            self.assertIn("if (bluefruit_configure_peripheral)", text)
            self.assertIn("VERIFY_STATUS ( " + call + ", false )", text)

    def test_selected_source_runtime_overlay(self):
        source = os.environ.get("OBSERVER_COC_SELECTED_SOURCE")
        if source is None:
            self.skipTest("set OBSERVER_COC_SELECTED_SOURCE for exact selected observer source")
        source = Path(source)
        patch = ROOT / "firmware/observer-coc-experimental/selected-544-runtime.patch"
        self.assertTrue(patch.is_file(), "selected-source CoC runtime overlay missing")
        paths = ["src/observer/ObserverTransport.h", "src/observer/ObserverTransport.cpp",
                 "src/helpers/nrf52/SerialBLEInterface.cpp", "examples/simple_repeater/main.cpp",
                 "variants/sensecap_solar/platformio.ini", "examples/simple_repeater/MyMesh.h"]
        with tempfile.TemporaryDirectory(prefix="observer-coc-runtime-overlay-") as directory:
            target = Path(directory)
            for relative in paths:
                original = source / relative
                self.assertTrue(original.is_file(), relative)
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(original, destination)
            applied = subprocess.run(["git", "apply", str(patch)], cwd=target,
                                     capture_output=True, text=True, timeout=10)
            self.assertEqual(applied.returncode, 0, applied.stderr)
            transport_h = (target / paths[0]).read_text()
            transport_cpp = (target / paths[1]).read_text()
            serial_ble = (target / paths[2]).read_text()
            main_loop = (target / paths[3]).read_text()
            config = (target / paths[4]).read_text()
            mesh_header = (target / paths[5]).read_text()
            version_block = mesh_header.split("#ifndef FIRMWARE_VERSION", 1)[1].split(
                '#define FIRMWARE_ROLE', 1)[0]
            for defines, expected in [
                ([], '"v1.18.0-obs544"'),
                (["-DSENSECAP_OBSERVER_COC_CHANNELS=0"], '"v1.18.0-obs544"'),
                (["-DSENSECAP_OBSERVER_COC_CHANNELS=1"], '"v1.18.0-coc"'),
                (["-DSENSECAP_OBSERVER_COC_CHANNELS=2"], '"v1.18.0-coc"'),
                (['-DFIRMWARE_VERSION="custom-release"'], '"custom-release"'),
            ]:
                preprocessed = subprocess.run(
                    ["g++", "-E", "-P", "-x", "c++", *defines, "-"],
                    input="#ifndef FIRMWARE_VERSION" + version_block + "\nFIRMWARE_VERSION\n",
                    capture_output=True, text=True, check=True,
                )
                self.assertEqual(preprocessed.stdout.strip(), expected)
            runtime_h = (target / "src/observer/ObserverBleRuntime.h").read_text()
            runtime_cpp = (target / "src/observer/ObserverBleRuntime.cpp").read_text()
            server_h = (target / "src/observer/coc/Server.h").read_text()
            for core in ("Server.h", "NordicPort.h", "Protocol.h", "Delivery.h", "S140Config.cpp"):
                self.assertTrue((target / "src/observer/coc" / core).is_file(), core)
            self.assertIn("coc::NordicPort", transport_h)
            self.assertIn("coc_server_.service", transport_cpp)
            self.assertNotIn("i == 0 && (!active_ || !ch_[1].ready", server_h)
            self.assertIn("if (!queue || !nonce || closing_ || load(c.binding) != kUnbound)", server_h)
            self.assertIn("if (!closing_ && ready()", server_h)
            self.assertIn("setObserverCallback", transport_cpp)
            self.assertIn("setLegacyCallback", serial_ble)
            self.assertIn("beginBluefruitOnce", serial_ble)
            self.assertIn("replyCoC", main_loop)
            self.assertIn("SoftwareTimer", transport_h)
            self.assertIn("SECMODE_ENC_WITH_MITM", transport_cpp)
            self.assertIn("discovery_characteristic_.setReadAuthorizeCallback(onDiscovery)", transport_cpp)
            self.assertIn("authorizeCoC(handle)", transport_cpp)
            self.assertIn("coc_timer_.setPeriod(delay_ms ? delay_ms : 1)", transport_cpp)
            self.assertIn("coc_server_.service(log_queue_, boot_nonce_", transport_cpp)
            self.assertIn("coc_server_.service(static_cast<Queue*>(nullptr)", transport_cpp)
            self.assertIn("if (bytes[index] == '\\n') cocWake();", transport_cpp)
            self.assertIn("static_cast<uint8_t>(coc::Server::kDataPsm)", transport_cpp)
            self.assertIn("#else\n  if (!Bluefruit.begin(1, 0))", transport_cpp)
            discovery_branch = transport_cpp.split("discovery_characteristic_.setProperties", 1)[1].split("#endif", 1)[0]
            self.assertIn("#else", discovery_branch)
            for legacy_gatt in ("log_characteristic_.begin()", "command_characteristic_.begin()",
                                "ack_characteristic_.begin()", "stats_characteristic_.begin()"):
                self.assertIn(legacy_gatt, discovery_branch)
            coc_gatt = transport_cpp.split("discovery_characteristic_.setProperties", 1)[1].split("#else", 1)[0]
            self.assertNotIn("log_characteristic_.begin()", coc_gatt)
            self.assertNotIn("command_characteristic_.begin()", coc_gatt)
            self.assertIn("#else\n  Bluefruit.setEventCallback(onBLEEvent);", serial_ble)
            self.assertIn("observer/coc/S140Config.cpp", config)
            self.assertEqual(runtime_cpp.count("Bluefruit.begin("), 1)
            self.assertIn("legacy_callback", runtime_cpp)
            self.assertIn("observer_callback", runtime_cpp)
            dispatch = runtime_cpp.split("void dispatch(ble_evt_t* event)", 1)[1].split("\n}", 1)[0]
            self.assertLess(dispatch.index("legacy(event)"), dispatch.index("observer(event)"))
            self.assertIn("Bluefruit.configPrphBandwidth(BANDWIDTH_MAX);", runtime_cpp)
            self.assertEqual(runtime_cpp.count("Bluefruit.setEventCallback("), 1)
            self.assertEqual(main_loop.count("board.sleep(0)"), 1)
            discovery_doc = ROOT / "firmware/observer-coc-experimental/DISCOVERY.md"
            self.assertIn("exactly 8 bytes", discovery_doc.read_text())

    def test_arm_core_and_optional_s140_config(self):
        compiler = os.environ.get("OBSERVER_COC_ARM_CXX")
        if compiler is None:
            self.skipTest("set OBSERVER_COC_ARM_CXX for Cortex-M4 object checks")
        self.assertTrue(Path(compiler).is_file(), "explicit ARM compiler does not exist")
        sdk = Path(os.environ["OBSERVER_COC_FRAMEWORK_SOURCE"])
        cmsis = Path(os.environ["OBSERVER_COC_CMSIS_INCLUDE"])
        self.assertTrue(cmsis.is_dir())
        includes = [sdk / "cores/nRF5/nordic/softdevice/s140_nrf52_7.3.0_API/include",
                    sdk / "cores/nRF5/nordic/nrfx/mdk", cmsis]
        base = [compiler, "-std=c++11", "-mcpu=cortex-m4", "-mthumb", "-Wall", "-Wextra",
                "-Werror", "-Wno-error=reorder", "-DNRF52840_XXAA", "-I", str(ROOT / "src")]
        for include in includes:
            self.assertTrue(include.is_dir())
            base += ["-isystem", str(include)]
        with tempfile.TemporaryDirectory(prefix="observer-coc-arm-") as directory:
            cases = [
                ("protocol", ROOT / "test/test_observer_coc/test_protocol.cpp", []),
                ("delivery", ROOT / "test/test_observer_coc/test_delivery.cpp", ["-DCOC_QUEUE_LIMIT=256"]),
            ]
            for count in (None, 1, 2):
                cases.append(("s140-" + str(count), ROOT / "src/observer/coc/S140Config.cpp",
                              [] if count is None else ["-DSENSECAP_OBSERVER_COC_CHANNELS=" + str(count)]))
            for name, source, flags in cases:
                output = Path(directory) / (name + ".o")
                result = subprocess.run(base + flags + ["-c", str(source), "-o", str(output)],
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(output.read_bytes()[18:20], bytes([40, 0]), "not an ARM ELF object")
                if name.startswith("s140-"):
                    symbols = subprocess.run([str(Path(compiler).with_name("arm-none-eabi-nm")),
                                              "--defined-only", str(output)],
                                             capture_output=True, text=True, timeout=10)
                    self.assertEqual(symbols.returncode, 0, symbols.stderr)
                    self.assertEqual("bluefruit_configure_peripheral" in symbols.stdout,
                                     name != "s140-None")
            invalid = subprocess.run(base + ["-DSENSECAP_OBSERVER_COC_CHANNELS=3", "-c",
                                     str(ROOT / "src/observer/coc/S140Config.cpp"),
                                     "-o", str(Path(directory) / "invalid.o")],
                                     capture_output=True, text=True, timeout=30)
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("one or two channels", invalid.stderr)


if __name__ == "__main__":
    unittest.main()

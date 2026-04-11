{ pkgs, ... }:
pkgs.testers.nixosTest {
  name = "stadtbibliothek-ha-integration";

  nodes.machine =
    { pkgs, ... }:
    {
      services.home-assistant = {
        enable = true;
        config = {
          # Minimal config to avoid network-dependent integrations
          homeassistant = {
            name = "Test";
            unit_system = "metric";
          };
        };
        customComponents = [
          (pkgs.stdenvNoCC.mkDerivation {
            pname = "ha-stadtbibliothek";
            version = "0.1.0";
            src = ../.;
            installPhase = ''
              mkdir -p $out/custom_components
              cp -r custom_components/stadtbibliothek $out/custom_components/
            '';
            passthru = {
              isHomeAssistantComponent = true;
              domain = "stadtbibliothek";
            };
          })
        ];
        extraPackages = ps: [
          ps.beautifulsoup4
          ps.html5lib
          ps.lxml
          ps.httpx
        ];
      };
    };

  testScript = ''
    machine.wait_for_unit("home-assistant.service")
    machine.wait_for_open_port(8123)

    # Verify the custom_components directory has our component
    machine.succeed("test -f /var/lib/hass/custom_components/stadtbibliothek/manifest.json")

    # Check that HA discovered our custom integration (no import errors)
    machine.wait_until_succeeds(
        "journalctl -u home-assistant.service | grep -q 'We found a custom integration stadtbibliothek'"
    )

    # Verify no import errors for our component
    machine.fail("journalctl -u home-assistant.service | grep -q 'Error loading.*stadtbibliothek'")
    machine.fail("journalctl -u home-assistant.service | grep -q 'ImportError.*stadtbibliothek'")
  '';
}

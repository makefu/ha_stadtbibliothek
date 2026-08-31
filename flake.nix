{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { nixpkgs, self, ... }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];

      # Every output is built against nixpkgs with our own overlay applied, so
      # `packages.default` and a consumer's `python3.withPackages` are the same
      # derivation rather than two recipes for one library.
      pkgsFor =
        system:
        import nixpkgs {
          inherit system;
          overlays = [ self.overlays.default ];
        };

      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f (pkgsFor system));

      version =
        (builtins.fromJSON (builtins.readFile ./custom_components/stadtbibliothek/manifest.json)).version;
    in
    {
      apps = forAllSystems (
        pkgs:
        let
          pythonWithDeps = pkgs.python313.withPackages (ps: [
            ps.httpx
            ps.beautifulsoup4
            ps.html5lib
            ps.lxml
            ps.pyyaml
          ]);
        in
        {
          stadtbibliothek-remseck = {
            type = "app";
            program = "${self.packages.${pkgs.system}.default}/bin/stadtbibliothek-remseck";
          };
          stadtbibliothek-stuttgart = {
            type = "app";
            program = "${self.packages.${pkgs.system}.default}/bin/stadtbibliothek-stuttgart";
          };
          integration-remseck = {
            type = "app";
            program = "${pkgs.writeShellScript "test-remseck" ''
              export PYTHONPATH=${./.}
              ${pythonWithDeps}/bin/python ${./.}/tests/integration/test_remseck_live.py "$@"
            ''}";
          };
          integration-stuttgart = {
            type = "app";
            program = "${pkgs.writeShellScript "test-stuttgart" ''
              if [ -z "''${SECRETS_FILE:-}" ]; then
                echo "ERROR: SECRETS_FILE environment variable must be set" >&2
                exit 1
              fi
              export PYTHONPATH=${./.}
              ${pythonWithDeps}/bin/python ${./.}/tests/integration/test_stuttgart_live.py "$@"
            ''}";
          };
          # Re-records tests/test_backends/fixtures/recorded/ from the live
          # OPACs. Writes into the working tree, not the store copy, so the
          # refreshed fixtures land where they can be committed.
          record-fixtures = {
            type = "app";
            program = "${pkgs.writeShellScript "record-fixtures" ''
              root="$(${pkgs.git}/bin/git rev-parse --show-toplevel)"
              export PYTHONPATH="$root"
              ${pythonWithDeps}/bin/python "$root/tests/integration/record_fixtures.py" "$@"
            ''}";
          };
        }
      );

      overlays.default = final: prev: {
        pythonPackagesExtensions = prev.pythonPackagesExtensions ++ [
          (pyfinal: _pyprev: {
            ha-stadtbibliothek = pyfinal.callPackage ./nix/package.nix { };
          })
        ];
      };

      packages = forAllSystems (pkgs: {
        # The Python module, for consumers that want to import the backends.
        python-module = pkgs.python313Packages.ha-stadtbibliothek;

        # The CLI tools, wrapping the very same build.
        default =
          (pkgs.python313Packages.toPythonApplication pkgs.python313Packages.ha-stadtbibliothek).overrideAttrs
            (old: {
              doInstallCheck = true;
              installCheckPhase = ''
                $out/bin/stadtbibliothek-remseck --version | grep -q "${version}"
                $out/bin/stadtbibliothek-stuttgart --version | grep -q "${version}"
              '';
              # home-assistant.customComponents reads this to find the component.
              passthru = old.passthru or { } // {
                isHomeAssistantComponent = true;
                domain = "stadtbibliothek";
              };
            });
      });

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python313.withPackages (ps: [
              ps.pytest
              ps.pytest-asyncio
              ps.httpx
              ps.beautifulsoup4
              ps.html5lib
              ps.lxml
              ps.respx
              ps.ruff
              ps.mypy
              ps.voluptuous
              ps.freezegun
            ]))
          ];
        };
      });

      checks = forAllSystems (
        pkgs:
        let
          # Use HA's python to get homeassistant + all its deps for type checking
          haPython = pkgs.home-assistant.python;
          pythonEnv = haPython.withPackages (ps: [
            ps.homeassistant
            ps.httpx
            ps.beautifulsoup4
            ps.html5lib
            ps.lxml
            ps.pyyaml
            ps.voluptuous
            ps.pytest
            ps.pytest-asyncio
            ps.respx
            ps.freezegun
          ]);
        in
        {
          ruff =
            pkgs.runCommand "ruff-check"
              {
                nativeBuildInputs = [ pkgs.ruff ];
                RUFF_CACHE_DIR = "/tmp/ruff-cache";
              }
              ''
                cd ${self}
                ruff check .
                ruff format --check .
                touch $out
              '';
          ty = pkgs.runCommand "ty-check" { nativeBuildInputs = [ pkgs.ty ]; } ''
            cd ${self}
            ty check --python ${pythonEnv}
            touch $out
          '';
          # Proves the overlay actually yields an importable module, so
          # downstream flakes find out here rather than in their own build.
          python-import =
            pkgs.runCommand "python-import-check"
              {
                nativeBuildInputs = [ (pkgs.python313.withPackages (ps: [ ps.ha-stadtbibliothek ])) ];
              }
              ''
                python -c 'from custom_components.stadtbibliothek.backends import BACKENDS, create_backend
                assert set(BACKENDS) == {"remseck", "stuttgart"}'
                touch $out
              '';
        }
        // nixpkgs.lib.optionalAttrs (pkgs.stdenv.hostPlatform.system == "x86_64-linux") {
          vm-test = import ./nix/vm-test.nix { inherit pkgs; };
        }
      );

      formatter = forAllSystems (pkgs: pkgs.nixfmt-rfc-style);
    };
}

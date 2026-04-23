{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { nixpkgs, self, ... }:
    let
      forAllSystems =
        f:
        nixpkgs.lib.genAttrs [
          "x86_64-linux"
          "aarch64-linux"
        ] (system: f nixpkgs.legacyPackages.${system});

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
        }
      );

      packages = forAllSystems (pkgs: {
        default = pkgs.python313Packages.buildPythonApplication {
          pname = "ha-stadtbibliothek";
          inherit version;
          src = ./.;
          format = "pyproject";

          build-system = [ pkgs.python313Packages.setuptools ];

          propagatedBuildInputs = with pkgs.python313Packages; [
            httpx
            beautifulsoup4
            html5lib
            lxml
          ];

          doInstallCheck = true;
          installCheckPhase = ''
            $out/bin/stadtbibliothek-remseck --version | grep -q "${version}"
            $out/bin/stadtbibliothek-stuttgart --version | grep -q "${version}"
          '';

          meta.mainProgram = "stadtbibliothek-remseck";

          passthru = {
            isHomeAssistantComponent = true;
            domain = "stadtbibliothek";
          };
        };
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
          ruff = pkgs.runCommand "ruff-check" {
            nativeBuildInputs = [ pkgs.ruff ];
            RUFF_CACHE_DIR = "/tmp/ruff-cache";
          } ''
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
        }
        // nixpkgs.lib.optionalAttrs (pkgs.stdenv.hostPlatform.system == "x86_64-linux") {
          vm-test = import ./nix/vm-test.nix { inherit pkgs; };
        }
      );

      formatter = forAllSystems (pkgs: pkgs.nixfmt-rfc-style);
    };
}

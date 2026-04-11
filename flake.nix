{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { nixpkgs, ... }:
    let
      forAllSystems =
        f:
        nixpkgs.lib.genAttrs [
          "x86_64-linux"
          "aarch64-linux"
        ] (system: f nixpkgs.legacyPackages.${system});
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
        default = pkgs.stdenvNoCC.mkDerivation {
          pname = "ha-stadtbibliothek";
          version = "0.1.0";
          src = ./.;
          installPhase = ''
            mkdir -p $out/custom_components
            cp -r custom_components/stadtbibliothek $out/custom_components/stadtbibliothek
          '';
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
              ps.respx
              ps.ruff
              ps.mypy
              ps.voluptuous
              ps.freezegun
            ]))
          ];
        };
      });

      formatter = forAllSystems (pkgs: pkgs.nixfmt-rfc-style);
    };
}

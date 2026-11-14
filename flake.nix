{
  description = "agentmap: a live portrait dashboard of T3 Code agent threads";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "aarch64-darwin" "x86_64-darwin" "aarch64-linux" "x86_64-linux" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in {
      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          # The application uses the Python standard library only.
          # coverage, pytest and mutmut are test tools.
          packages = [
            pkgs.python3
            pkgs.python3Packages.coverage
            pkgs.python3Packages.pytest
            pkgs.mutmut
            pkgs.sqlite
            pkgs.git
          ];
        };
      });
    };
}

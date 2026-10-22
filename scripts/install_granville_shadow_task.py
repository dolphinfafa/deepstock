"""Restore the existing Granville schedule after a real S4U compatibility probe."""
from scripts.install_observation_tasks import install


def main():
    install(('Deepstock_Granville_Shadow',))


if __name__ == '__main__':
    main()

# -*- coding: utf-8 -*- 
#!/usr/bin/env python
import logging as logger
import click

from pyboy import PyBoy

# parse command line
@click.command()
@click.option('--file', type=str, required=True, help='rom file')
@click.option('--debug', type=bool, required=False, is_flag=True, help='print debug log information')
def main(file: str, debug: bool) -> None:
	if debug:
		logger.basicConfig(format='[%(asctime)s][%(levelname)s] %(message)s', level=logger.DEBUG)
	else:
		logger.basicConfig(format='[%(asctime)s][%(levelname)s] %(message)s', level=logger.INFO)
	# Application
	pyboy = PyBoy(file)
	while pyboy.tick():
		pass
	pyboy.stop()	

if __name__ == "__main__":
	main()

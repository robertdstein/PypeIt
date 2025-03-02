.. code-block:: console

    $ pypeit_identify -h
    usage: pypeit_identify [-h] [--lamps LAMPS] [-s] [--wmin WMIN] [--wmax WMAX]
                           [--slit SLIT] [--det DET] [--rmstol RMSTOL] [--fwhm FWHM]
                           [--sigdetect SIGDETECT] [--pixtol PIXTOL] [--test]
                           [--linear] [--force_save] [--rescale_resid]
                           arc_file slits_file
    
    Launch PypeIt identify tool, display extracted MasterArc, and load linelist.Run
    above the Masters/ folder.
    
    positional arguments:
      arc_file              PypeIt MasterArc file
      slits_file            PypeIt MasterSlits file
    
    optional arguments:
      -h, --help            show this help message and exit
      --lamps LAMPS         Comma separated list of calibration lamps (no spaces)
                            (default: None)
      -s, --solution        Load a wavelength solution from the arc_file (if it
                            exists) (default: False)
      --wmin WMIN           Minimum wavelength range (default: 3000.0)
      --wmax WMAX           Maximum wavelength range (default: 26000.0)
      --slit SLIT           Which slit to load for wavelength calibration (default:
                            0)
      --det DET             Detector index (default: 1)
      --rmstol RMSTOL       RMS tolerance (default: 0.1)
      --fwhm FWHM           FWHM for line finding (default: 4.0)
      --sigdetect SIGDETECT
                            sigma detection for line finding (default: None)
      --pixtol PIXTOL       Pixel tolerance for Auto IDs (default: 0.1)
      --test                Testing functionality, do not show plots (default:
                            False)
      --linear              Show the spectrum in linear (rather than log) scale
                            (default: False)
      --force_save          Save the solutions, despite the RMS (default: False)
      --rescale_resid       Rescale the residual plot to include all points?
                            (default: False)
    
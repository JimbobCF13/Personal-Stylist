# V4.8.1 startup hotfix

- Fixes Render startup failure: `NameError: name 're' is not defined`.
- Adds the missing Python `re` import used by wardrobe category normalisation.
- Corrects taxonomy precedence so polos/T-shirts are classified before generic shirts, and overshirts before shirts.
- No database reset or garment deletion. Existing wardrobe records are normalised in place on startup.

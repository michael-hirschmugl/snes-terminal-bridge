; =============================================================================
; SNES Joypad Visualizer — debug ROM
;
; Displays the raw joypad state of controller 1 on screen row 0:
;
;   Columns 0–7:  bits 7–0 of $4219 (B, Y, Sel, Start, Up, Down, Left, Right)
;   Columns 8–15: bits 7–0 of $4218 (A, X, L, R, 0, 0, 0, 0)
;
;   '*' = bit set (button pressed)
;   ' ' = bit clear (button released)
;
; Updated every VBlank (~60 Hz) using sequential VRAM writes.
; No keymap lookup, no debounce — raw signal only.
;
; VRAM layout:
;   $0000–$07FF  BG1 tilemap  (32×32 entries × 2 bytes = 2 KB)
;   $1000–$1BFF  Font tiles   (384 subtiles × 16 bytes = 6 KB)
;
; BG mode: Mode 0, BG1 (16×16 tiles, 2bpp, 4 colours)
; Palette:  colour 0 = black ($0000), colour 1 = white ($7FFF)
; =============================================================================

.setcpu "65816"

; -----------------------------------------------------------------------------
; Hardware registers
; -----------------------------------------------------------------------------

INIDISP  = $2100
BGMODE   = $2105
BG1SC    = $2107
BG12NBA  = $210B
TM       = $212C
CGADD    = $2121
CGDATA   = $2122
VMAIN    = $2115
VMADDL   = $2116
VMADDH   = $2117
VMDATAL  = $2118
VMDATAH  = $2119
HVBJOY   = $4212   ; bit 7: VBlank active, bit 0: auto-joypad busy
NMITIMEN = $4200
JOY1L    = $4218   ; controller 1 low  byte: A, X, L, R, 0, 0, 0, 0
JOY1H    = $4219   ; controller 1 high byte: B, Y, Sel, Start, Up, Dn, Left, Right
DMAP0    = $4300
BBAD0    = $4301
A1TL0    = $4302
A1TH0    = $4303
A1B0     = $4304
DAS0L    = $4305
DAS0H    = $4306
MDMAEN   = $420B

; Tile data constants (must match gen_font.py)
NUM_GROUPS  = 12
TOTAL_TILES = NUM_GROUPS * 32
FONT_BYTES  = TOTAL_TILES * 16   ; 6144 bytes

TILEMAP_BYTES = 1024 * 2         ; 2048 bytes

; Tile numbers for indicator characters
TILE_STAR  = 36     ; '*' = ASCII 0x2A → C=10 → tile=(10/8)*32+(10%8)*2 = 36
TILE_SPACE = 0      ; ' ' = ASCII 0x20 → tile = 0

; Direct-page scratch
joy_hi   = $00      ; byte — snapshot of $4219 while writing
joy_lo   = $01      ; byte — snapshot of $4218 while writing

; -----------------------------------------------------------------------------
; CODE
; -----------------------------------------------------------------------------

.segment "CODE"

nmi_handler:
irq_handler:
cop_handler:
brk_handler:
abort_handler:
    rti

reset:
    sei
    clc
    xce                         ; → native (65816) mode

    rep     #$30                ; A=16-bit, X=16-bit
    .a16
    .i16
    lda     #$1FFF
    tcs                         ; stack = $1FFF
    lda     #$0000
    tcd                         ; direct page = $0000

    sep     #$20                ; A=8-bit
    .a8

    lda     #$8F                ; force blank
    sta     INIDISP
    stz     NMITIMEN

    ; -------------------------------------------------------------------------
    ; Palette: colour 0 = black, colour 1 = white
    ; -------------------------------------------------------------------------
    stz     CGADD
    stz     CGDATA
    stz     CGDATA
    lda     #$FF
    sta     CGDATA
    lda     #$7F
    sta     CGDATA

    lda     #$80
    sta     VMAIN

    ; =========================================================================
    ; DMA 1 — tilemap → VRAM $0000
    ; =========================================================================
    stz     VMADDL
    stz     VMADDH

    lda     #$01
    sta     DMAP0
    lda     #$18
    sta     BBAD0
    lda     #<tilemap_data
    sta     A1TL0
    lda     #>tilemap_data
    sta     A1TH0
    lda     #^tilemap_data
    sta     A1B0

    rep     #$20
    .a16
    lda     #TILEMAP_BYTES
    sta     DAS0L
    sep     #$20
    .a8

    lda     #$01
    sta     MDMAEN

    ; =========================================================================
    ; DMA 2 — font tiles → VRAM $1000
    ; =========================================================================
    stz     VMADDL
    lda     #$10
    sta     VMADDH

    lda     #<font_tiles
    sta     A1TL0
    lda     #>font_tiles
    sta     A1TH0
    lda     #^font_tiles
    sta     A1B0

    rep     #$20
    .a16
    lda     #FONT_BYTES
    sta     DAS0L
    sep     #$20
    .a8

    lda     #$01
    sta     MDMAEN

    ; =========================================================================
    ; BG configuration
    ; =========================================================================
    lda     #$10                ; Mode 0, BG1 16×16 tiles
    sta     BGMODE
    lda     #$00                ; BG1 tilemap at $0000
    sta     BG1SC
    lda     #$01                ; BG1 tile data at $1000
    sta     BG12NBA
    lda     #$01                ; enable BG1
    sta     TM

    ; =========================================================================
    ; Write label row to tilemap row 1 (VRAM word $0020–$002F)
    ; Labels: B  Y  S  T  ^  v  <  >  A  X  L  R  -  -  -  -
    ; =========================================================================
    lda     #$20                ; word address $0020 = row 1, col 0
    sta     VMADDL
    stz     VMADDH

    ; 16-bit A: write each tile word to VMDATAL — the store hits $2118+$2119
    ; which increments the VRAM address after the $2119 (VMDATAH) write.
    rep     #$20
    .a16
    lda     #132
    sta     VMDATAL             ; B
    lda     #226
    sta     VMDATAL             ; Y
    lda     #198
    sta     VMDATAL             ; S  (Select)
    lda     #200
    sta     VMDATAL             ; T  (Start)
    lda     #236
    sta     VMDATAL             ; ^  (Up)
    lda     #332
    sta     VMDATAL             ; v  (Down)
    lda     #104
    sta     VMDATAL             ; <  (Left)
    lda     #108
    sta     VMDATAL             ; >  (Right)
    lda     #130
    sta     VMDATAL             ; A
    lda     #224
    sta     VMDATAL             ; X
    lda     #168
    sta     VMDATAL             ; L
    lda     #196
    sta     VMDATAL             ; R
    lda     #42
    sta     VMDATAL             ; - (unused col 12)
    lda     #42
    sta     VMDATAL             ; - (unused col 13)
    lda     #42
    sta     VMDATAL             ; - (unused col 14)
    lda     #42
    sta     VMDATAL             ; - (unused col 15)
    sep     #$20
    .a8

    ; Enable auto-joypad read
    lda     #$01
    sta     NMITIMEN

    lda     #$0F                ; display on
    sta     INIDISP

; =============================================================================
; Main loop — update row 0 every VBlank with raw joypad bits
; =============================================================================

@main_loop:

    ; -------------------------------------------------------------------------
    ; Wait for VBlank start
    ; -------------------------------------------------------------------------
@wait_vblank:
    lda     HVBJOY
    and     #$80
    beq     @wait_vblank

    ; -------------------------------------------------------------------------
    ; Wait for auto-joypad read to finish
    ; -------------------------------------------------------------------------
@wait_joy:
    lda     HVBJOY
    and     #$01
    bne     @wait_joy

    ; Snapshot joypad registers
    lda     JOY1H
    sta     joy_hi              ; $4219: B,Y,Sel,Start,Up,Dn,Left,Right
    lda     JOY1L
    sta     joy_lo              ; $4218: A,X,L,R,...

    ; -------------------------------------------------------------------------
    ; Set VRAM address to tilemap word 0 (row 0, col 0)
    ; -------------------------------------------------------------------------
    stz     VMADDL
    stz     VMADDH

    ; -------------------------------------------------------------------------
    ; Write 8 tiles for $4219 bits (cols 0–7)
    ; -------------------------------------------------------------------------
    ldx     #8
@loop_hi:
    asl     joy_hi              ; MSB → carry
    bcc     @hi_clear
    lda     #<TILE_STAR
    sta     VMDATAL
    lda     #>TILE_STAR
    sta     VMDATAH
    bra     @hi_next
@hi_clear:
    stz     VMDATAL
    stz     VMDATAH
@hi_next:
    dex
    bne     @loop_hi

    ; -------------------------------------------------------------------------
    ; Write 8 tiles for $4218 bits (cols 8–15)
    ; -------------------------------------------------------------------------
    ldx     #8
@loop_lo:
    asl     joy_lo              ; MSB → carry
    bcc     @lo_clear
    lda     #<TILE_STAR
    sta     VMDATAL
    lda     #>TILE_STAR
    sta     VMDATAH
    bra     @lo_next
@lo_clear:
    stz     VMDATAL
    stz     VMDATAH
@lo_next:
    dex
    bne     @loop_lo

    jmp     @main_loop

; -----------------------------------------------------------------------------
; Data
; -----------------------------------------------------------------------------

.segment "RODATA"

tilemap_data:
.include "../assets/tilemap.inc"

font_tiles:
.include "../assets/font.inc"

; -----------------------------------------------------------------------------
; SNES internal header  ($FFC0–$FFE3)
; -----------------------------------------------------------------------------

.segment "HEADER"
    .byte "SNES TERMINAL       "
    .byte $20
    .byte $00
    .byte $05
    .byte $00
    .byte $01
    .byte $00
    .byte $00
    .word $FFFF
    .word $0000
    .byte $00, $00, $00, $00

; -----------------------------------------------------------------------------
; Interrupt vectors  ($FFE4–$FFFF)
; -----------------------------------------------------------------------------

.segment "VECTORS"
    .word cop_handler
    .word brk_handler
    .word abort_handler
    .word nmi_handler
    .word $0000
    .word irq_handler
    .word $0000
    .word $0000
    .word cop_handler
    .word $0000
    .word abort_handler
    .word nmi_handler
    .word reset
    .word irq_handler
